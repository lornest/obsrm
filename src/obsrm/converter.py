"""Document conversion via Pandoc."""

import shutil
import subprocess
import tempfile
from pathlib import Path

from obsrm.markdown_processor import process_markdown


class ConversionError(Exception):
    pass


def convert_file(
    file_path: Path,
    vault_path: Path,
    output_format: str = "epub",
    output_dir: Path | None = None,
    filters_dir: Path | None = None,
    styles_dir: Path | None = None,
) -> Path:
    """Convert an Obsidian markdown file to ePub or PDF via Pandoc.

    Args:
        file_path: absolute path to the markdown file
        vault_path: root of the Obsidian vault
        output_format: "epub" or "pdf"
        output_dir: directory for output files (defaults to temp dir)
        filters_dir: directory containing Lua filters
        styles_dir: directory containing CSS stylesheets

    Returns:
        Path to the generated output file.

    Raises:
        ConversionError: if Pandoc is not found or conversion fails.
    """
    if not shutil.which("pandoc"):
        raise ConversionError(
            "Pandoc is not installed. Install it from https://pandoc.org/installing.html"
        )

    try:
        content = file_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        # Fall back to latin-1 which accepts any byte sequence
        content = file_path.read_text(encoding="latin-1")
    processed, title, images = process_markdown(
        content, file_path, vault_path, output_format=output_format
    )

    if title is None:
        title = file_path.stem

    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="obsidian-sync-"))

    # Stage images into a working directory so Pandoc can find them
    work_dir = output_dir / f"{file_path.stem}_work"
    work_dir.mkdir(exist_ok=True)
    for src_path, staged_name in images:
        dest = work_dir / staged_name
        if not dest.exists():
            shutil.copy2(src_path, dest)

    extension = "epub" if output_format == "epub" else "pdf"
    output_path = output_dir / f"{file_path.stem}.{extension}"

    # Write preprocessed markdown to the work dir so image refs resolve
    tmp_md = work_dir / f"{file_path.stem}.md"
    tmp_md.write_text(processed, encoding="utf-8")

    cmd = [
        "pandoc",
        str(tmp_md),
        "-o",
        str(output_path),
        # Disable yaml_metadata_block: we already extract frontmatter ourselves,
        # and leftover --- in content can cause YAML parse errors
        "--from",
        "markdown+wikilinks_title_after_pipe-yaml_metadata_block",
        "--metadata",
        f"title={title}",
        "--resource-path",
        str(work_dir),
    ]

    if output_format == "epub":
        cmd.extend(["--to", "epub3", "--epub-title-page=false"])
    else:
        # PDF tuned for reMarkable e-ink display (1872x1404 @ 226 DPI ≈ 8.3" x 6.2")
        # Use A5 landscape which closely matches the aspect ratio
        cmd.extend(
            [
                "--pdf-engine=xelatex",
                "-H",
                _latex_preamble_path(),
                "-V",
                "geometry:paperwidth=6.2in",
                "-V",
                "geometry:paperheight=8.3in",
                "-V",
                "geometry:margin=0.6in",
                "-V",
                "fontsize=11pt",
                "-V",
                "linestretch=1.4",
            ]
        )

    # Add Lua filter if available
    if filters_dir is None:
        filters_dir = _find_package_dir() / "filters"
    lua_filter = filters_dir / "obsidian.lua"
    if lua_filter.exists():
        cmd.extend(["--lua-filter", str(lua_filter)])

    # Add CSS stylesheet if available
    if styles_dir is None:
        styles_dir = _find_package_dir() / "styles"
    css_file = styles_dir / "remarkable.css"
    if css_file.exists():
        cmd.extend(["--css", str(css_file)])

    result = subprocess.run(cmd, capture_output=True, text=True)

    # Clean up work directory
    shutil.rmtree(work_dir, ignore_errors=True)

    if result.returncode != 0:
        raise ConversionError(f"Pandoc conversion failed:\n{result.stderr}")

    return output_path


def _latex_preamble_path() -> str:
    """Return path to the XeLaTeX preamble file for PDF output."""
    preamble = _find_package_dir() / "styles" / "preamble.tex"
    return str(preamble)


def _find_package_dir() -> Path:
    """Find the package source directory."""
    return Path(__file__).parent
