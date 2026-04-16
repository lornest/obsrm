"""Reverse sync: pull files from reMarkable to Obsidian vault."""

import logging
import shutil
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from obsrm.remarkable import RemarkableClient, RmapiError

logger = logging.getLogger(__name__)


def list_remote_files(client: RemarkableClient, target_folder: str) -> tuple[dict[str, str], bool]:
    """List all files (not directories) under the target folder.

    Returns (files_dict, listing_complete) where files_dict maps
    remote_path -> 'f' for files only, and listing_complete is False
    if any subfolders failed to list.

    If the target folder doesn't exist yet (first run), it is created
    and an empty dict is returned.
    """
    errors: list[str] = []
    try:
        all_entries = client.list_recursive(target_folder, errors)
    except RmapiError:
        logger.info("Target folder %s not found, creating it", target_folder)
        client.ensure_folder(target_folder)
        return {}, True
    files = {path: entry_type for path, entry_type in all_entries.items() if entry_type == "f"}
    return files, len(errors) == 0


def remote_path_to_vault_rel(remote_path: str, target_folder: str) -> str:
    """Convert a remote path to a vault-relative path.

    E.g. /Obsidian/GL/Notes -> GL/Notes
    """
    rel = remote_path[len(target_folder) :].lstrip("/")
    return rel


def pull_file(
    client: RemarkableClient,
    remote_path: str,
    vault_path: Path,
    target_folder: str,
    attachments_folder: str,
) -> tuple[Path, Path | None]:
    """Download a file from reMarkable and create a vault entry.

    For notebooks with typed text: extracts text directly to markdown.
    For annotated documents or handwritten-only notebooks: embeds as PDF/SVG.

    Returns (markdown_path, attachment_path_or_none).
    """
    rel = remote_path_to_vault_rel(remote_path, target_folder)
    name = Path(rel).name
    rel_dir = Path(rel).parent

    md_dir = vault_path / rel_dir if rel_dir != Path(".") else vault_path
    md_dir.mkdir(parents=True, exist_ok=True)
    md_path = md_dir / f"{name}.md"

    tmp_dir = Path(tempfile.mkdtemp(prefix="obsidian-pull-"))
    try:
        downloaded = client.download(remote_path, tmp_dir)

        # If it's an .rmdoc (notebook), try to extract typed text
        if downloaded.suffix == ".rmdoc":
            return _handle_notebook(
                downloaded,
                md_path,
                name,
                vault_path,
                rel_dir,
                attachments_folder,
            )

        # Otherwise it's a PDF — embed it
        return _handle_pdf(
            downloaded,
            md_path,
            name,
            vault_path,
            rel_dir,
            attachments_folder,
        )
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _handle_notebook(
    rmdoc_path: Path,
    md_path: Path,
    name: str,
    vault_path: Path,
    rel_dir: Path,
    attachments_folder: str,
) -> tuple[Path, Path | None]:
    """Handle a notebook (.rmdoc) file.

    Extracts typed text to markdown. If no text is found (pure handwriting),
    falls back to SVG rendering embedded in markdown.
    """
    from obsrm.rm_extract import extract_text_from_rmdoc, pages_to_markdown

    pages = extract_text_from_rmdoc(rmdoc_path)

    if pages:
        # Has typed text — create markdown directly
        md_content = pages_to_markdown(pages, name)
        if md_path.exists():
            _append_annotation_text(md_path, md_content)
        else:
            md_path.write_text(md_content, encoding="utf-8")
        return md_path, None

    # No typed text (pure handwriting) — render as SVG and embed
    try:
        import zipfile

        from rmc import rm_to_svg

        attachments_dir = vault_path / attachments_folder
        if rel_dir != Path("."):
            attachments_dir = attachments_dir / rel_dir
        attachments_dir.mkdir(parents=True, exist_ok=True)

        svgs = []
        with zipfile.ZipFile(rmdoc_path) as zf:
            rm_files = sorted(n for n in zf.namelist() if n.endswith(".rm"))
            for i, rm_name in enumerate(rm_files):
                svg_name = f"{name}_p{i + 1}.svg"
                svg_path = attachments_dir / svg_name
                with zf.open(rm_name) as f:
                    # Write to a temp file since rm_to_svg needs a file path
                    tmp_rm = rmdoc_path.parent / "temp.rm"
                    tmp_rm.write_bytes(f.read())
                    rm_to_svg(tmp_rm, svg_path)
                    tmp_rm.unlink()
                svgs.append(svg_path)

        if svgs:
            lines = [f"# {name}\n"]
            for svg_path in svgs:
                svg_rel = svg_path.relative_to(vault_path)
                lines.append(f"![[{svg_rel.as_posix()}]]\n")

            if md_path.exists():
                _append_annotation_text(md_path, "\n".join(lines))
            else:
                md_path.write_text("\n".join(lines), encoding="utf-8")
            return md_path, attachments_dir

    except (ImportError, Exception) as e:
        logger.warning("Could not render SVG for %s: %s", name, e)

    # Last resort: store the rmdoc as-is
    attachments_dir = vault_path / attachments_folder
    if rel_dir != Path("."):
        attachments_dir = attachments_dir / rel_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)
    att_path = attachments_dir / f"{name}.rmdoc"
    shutil.copy2(rmdoc_path, att_path)
    att_rel = att_path.relative_to(vault_path)

    if not md_path.exists():
        md_path.write_text(
            f"# {name}\n\n*Handwritten notebook — raw file attached.*\n\n"
            f"![[{att_rel.as_posix()}]]\n",
            encoding="utf-8",
        )
    return md_path, att_path


def _handle_pdf(
    pdf_path: Path,
    md_path: Path,
    name: str,
    vault_path: Path,
    rel_dir: Path,
    attachments_folder: str,
) -> tuple[Path, Path]:
    """Handle a PDF file — embed in markdown."""
    attachments_dir = vault_path / attachments_folder
    if rel_dir != Path("."):
        attachments_dir = attachments_dir / rel_dir
    attachments_dir.mkdir(parents=True, exist_ok=True)

    att_path = attachments_dir / f"{name}.pdf"
    shutil.copy2(pdf_path, att_path)
    att_rel = att_path.relative_to(vault_path)

    if md_path.exists():
        _append_annotation_link(md_path, att_rel)
    else:
        md_path.write_text(
            f"# {name}\n\n![[{att_rel.as_posix()}]]\n",
            encoding="utf-8",
        )
    return md_path, att_path


def _append_annotation_link(md_path: Path, pdf_rel: Path) -> None:
    """Append an annotations section to an existing markdown file."""
    content = md_path.read_text(encoding="utf-8")
    annotation_marker = "## Annotations"
    embed = f"![[{pdf_rel.as_posix()}]]"

    if embed in content:
        return

    if annotation_marker in content:
        lines = content.splitlines(keepends=True)
        new_lines = []
        found_marker = False
        replaced = False
        for line in lines:
            if annotation_marker in line:
                found_marker = True
            if found_marker and not replaced and line.strip().startswith("![["):
                new_lines.append(f"{embed}\n")
                replaced = True
                continue
            new_lines.append(line)
        if not replaced:
            new_lines.append(f"\n{embed}\n")
        md_path.write_text("".join(new_lines), encoding="utf-8")
    else:
        if not content.endswith("\n"):
            content += "\n"
        content += f"\n{annotation_marker}\n\n{embed}\n"
        md_path.write_text(content, encoding="utf-8")


def _append_annotation_text(md_path: Path, new_content: str) -> None:
    """Append pulled content to an existing markdown file."""
    content = md_path.read_text(encoding="utf-8")
    marker = "## From reMarkable"

    if new_content.strip() in content:
        return

    if not content.endswith("\n"):
        content += "\n"
    content += f"\n{marker}\n\n{new_content}\n"
    md_path.write_text(content, encoding="utf-8")


def now_iso() -> str:
    return datetime.now(UTC).isoformat()
