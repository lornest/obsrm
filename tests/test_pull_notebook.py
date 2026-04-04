"""Tests for pull.py notebook paths, pull_file dispatch, and _append_annotation_text."""

import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch

from obsidian_remarkable_sync.pull import (  # noqa: I001
    _append_annotation_text,
    _handle_notebook,
    pull_file,
)

# --- _append_annotation_text ---


def test_append_annotation_text_to_new_file(tmp_path):
    """When md doesn't exist yet, _handle_notebook writes directly, but
    _append_annotation_text is used when md already exists."""
    md_path = tmp_path / "Note.md"
    md_path.write_text("# Existing\n\nSome content.\n")

    _append_annotation_text(md_path, "New text from reMarkable")

    content = md_path.read_text()
    assert "# Existing" in content
    assert "## From reMarkable" in content
    assert "New text from reMarkable" in content


def test_append_annotation_text_idempotent(tmp_path):
    md_path = tmp_path / "Note.md"
    md_path.write_text("# Note\n")

    _append_annotation_text(md_path, "Typed content")
    _append_annotation_text(md_path, "Typed content")

    content = md_path.read_text()
    assert content.count("Typed content") == 1


def test_append_annotation_text_no_trailing_newline(tmp_path):
    md_path = tmp_path / "Note.md"
    md_path.write_text("# Note")  # no trailing newline

    _append_annotation_text(md_path, "New stuff")

    content = md_path.read_text()
    assert "## From reMarkable" in content
    assert "New stuff" in content


# --- _handle_notebook with typed text ---


def test_handle_notebook_typed_text_creates_md(tmp_path):
    """When extract_text_from_rmdoc returns pages, markdown is written directly."""
    rmdoc = tmp_path / "source.rmdoc"
    rmdoc.write_bytes(b"fake")  # won't be read by mocked extractor

    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "MyNote.md"

    pages = [{"paragraphs": [{"text": "Hello from notebook", "style": "plain"}]}]

    with (
        patch("obsidian_remarkable_sync.rm_extract.extract_text_from_rmdoc", return_value=pages),
        patch(
            "obsidian_remarkable_sync.rm_extract.pages_to_markdown",
            return_value="# MyNote\n\nHello from notebook\n",
        ),
    ):
        md, att = _handle_notebook(rmdoc, md_path, "MyNote", vault, Path("."), "attachments")

    assert md == md_path
    assert att is None
    content = md_path.read_text()
    assert "Hello from notebook" in content


def test_handle_notebook_typed_text_appends_to_existing(tmp_path):
    """When md already exists and notebook has typed text, it appends."""
    rmdoc = tmp_path / "source.rmdoc"
    rmdoc.write_bytes(b"fake")

    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "MyNote.md"
    md_path.write_text("# Existing note\n\nOld content.\n")

    pages = [{"paragraphs": [{"text": "New typed text", "style": "plain"}]}]

    with (
        patch("obsidian_remarkable_sync.rm_extract.extract_text_from_rmdoc", return_value=pages),
        patch(
            "obsidian_remarkable_sync.rm_extract.pages_to_markdown",
            return_value="New typed text\n",
        ),
    ):
        md, att = _handle_notebook(rmdoc, md_path, "MyNote", vault, Path("."), "attachments")

    content = md_path.read_text()
    assert "Existing note" in content
    assert "## From reMarkable" in content
    assert "New typed text" in content


# --- _handle_notebook raw .rmdoc fallback ---


def test_handle_notebook_raw_fallback(tmp_path):
    """When no typed text and SVG rendering fails, stores raw .rmdoc."""
    rmdoc = tmp_path / "source.rmdoc"
    # Create a valid zip so the SVG path can attempt to open it
    with zipfile.ZipFile(rmdoc, "w") as zf:
        zf.writestr("metadata.json", "{}")

    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "MyNote.md"

    with patch("obsidian_remarkable_sync.rm_extract.extract_text_from_rmdoc", return_value=[]):
        # rmc import will fail (ImportError), triggering raw fallback
        md, att = _handle_notebook(rmdoc, md_path, "MyNote", vault, Path("."), "attachments")

    assert md == md_path
    assert att is not None
    assert att.suffix == ".rmdoc"
    assert att.exists()

    content = md_path.read_text()
    assert "Handwritten notebook" in content
    assert "![[" in content


def test_handle_notebook_raw_fallback_existing_md(tmp_path):
    """When md already exists and raw fallback is used, md is not overwritten."""
    rmdoc = tmp_path / "source.rmdoc"
    with zipfile.ZipFile(rmdoc, "w") as zf:
        zf.writestr("metadata.json", "{}")

    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "MyNote.md"
    md_path.write_text("# Original content\n")

    with patch("obsidian_remarkable_sync.rm_extract.extract_text_from_rmdoc", return_value=[]):
        md, att = _handle_notebook(rmdoc, md_path, "MyNote", vault, Path("."), "attachments")

    content = md_path.read_text()
    assert "Original content" in content
    # Should NOT have been overwritten with the handwritten template
    assert "Handwritten notebook" not in content


def test_handle_notebook_raw_fallback_with_subdirectory(tmp_path):
    """Raw fallback respects rel_dir for attachment placement."""
    rmdoc = tmp_path / "source.rmdoc"
    with zipfile.ZipFile(rmdoc, "w") as zf:
        zf.writestr("metadata.json", "{}")

    vault = tmp_path / "vault"
    vault.mkdir()
    md_dir = vault / "sub"
    md_dir.mkdir()
    md_path = md_dir / "MyNote.md"

    with patch("obsidian_remarkable_sync.rm_extract.extract_text_from_rmdoc", return_value=[]):
        md, att = _handle_notebook(rmdoc, md_path, "MyNote", vault, Path("sub"), "attachments")

    assert att.parent == vault / "attachments" / "sub"


# --- _handle_notebook SVG fallback ---


def test_handle_notebook_svg_fallback(tmp_path):
    """When no typed text but rmc is available, renders SVGs."""
    rmdoc = tmp_path / "source.rmdoc"
    with zipfile.ZipFile(rmdoc, "w") as zf:
        zf.writestr("page1.rm", b"fake rm data")
        zf.writestr("page2.rm", b"fake rm data")

    vault = tmp_path / "vault"
    vault.mkdir()
    md_path = vault / "MyNote.md"

    def fake_rm_to_svg(rm_path, svg_path):
        svg_path.write_text("<svg>fake</svg>")

    with (
        patch("obsidian_remarkable_sync.rm_extract.extract_text_from_rmdoc", return_value=[]),
        patch.dict("sys.modules", {"rmc": MagicMock(rm_to_svg=fake_rm_to_svg)}),
    ):
        md, att = _handle_notebook(rmdoc, md_path, "MyNote", vault, Path("."), "attachments")

    content = md_path.read_text()
    assert "# MyNote" in content
    assert "MyNote_p1.svg" in content
    assert "MyNote_p2.svg" in content
    assert att == vault / "attachments"


# --- pull_file dispatch ---


def test_pull_file_dispatches_to_pdf(tmp_path):
    """pull_file routes .pdf downloads to _handle_pdf."""
    vault = tmp_path / "vault"
    vault.mkdir()

    client = MagicMock()
    pdf_file = tmp_path / "dl" / "MyNote.pdf"
    pdf_file.parent.mkdir()
    pdf_file.write_bytes(b"%PDF-1.4 fake")
    client.download.return_value = pdf_file

    with (
        patch("obsidian_remarkable_sync.pull.tempfile.mkdtemp", return_value=str(tmp_path / "dl")),
        patch("obsidian_remarkable_sync.pull.shutil.rmtree"),
    ):
        md, att = pull_file(client, "/Test/MyNote", vault, "/Test", "attachments")

    assert md.name == "MyNote.md"
    assert att.suffix == ".pdf"
    content = md.read_text()
    assert "![[" in content


def test_pull_file_dispatches_to_notebook(tmp_path):
    """pull_file routes .rmdoc downloads to _handle_notebook."""
    vault = tmp_path / "vault"
    vault.mkdir()

    client = MagicMock()
    rmdoc_file = tmp_path / "dl" / "MyNote.rmdoc"
    rmdoc_file.parent.mkdir()
    # Create a valid zip
    with zipfile.ZipFile(rmdoc_file, "w") as zf:
        zf.writestr("metadata.json", "{}")
    client.download.return_value = rmdoc_file

    with (
        patch(
            "obsidian_remarkable_sync.pull.tempfile.mkdtemp",
            return_value=str(tmp_path / "dl"),
        ),
        patch("obsidian_remarkable_sync.pull.shutil.rmtree"),
        patch("obsidian_remarkable_sync.rm_extract.extract_text_from_rmdoc", return_value=[]),
    ):
        md, att = pull_file(client, "/Test/MyNote", vault, "/Test", "attachments")

    assert md.name == "MyNote.md"
    # Raw .rmdoc fallback since no typed text and no rmc
    assert att.suffix == ".rmdoc"


def test_pull_file_with_nested_path(tmp_path):
    """pull_file creates subdirectories matching the remote path structure."""
    vault = tmp_path / "vault"
    vault.mkdir()

    client = MagicMock()
    pdf_file = tmp_path / "dl" / "Deep.pdf"
    pdf_file.parent.mkdir()
    pdf_file.write_bytes(b"%PDF")
    client.download.return_value = pdf_file

    with (
        patch(
            "obsidian_remarkable_sync.pull.tempfile.mkdtemp",
            return_value=str(tmp_path / "dl"),
        ),
        patch("obsidian_remarkable_sync.pull.shutil.rmtree"),
    ):
        md, att = pull_file(client, "/Test/Sub/Deep", vault, "/Test", "attachments")

    assert md == vault / "Sub" / "Deep.md"
    assert (vault / "Sub").is_dir()
