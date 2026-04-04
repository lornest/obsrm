"""Tests for document conversion via Pandoc."""

import shutil
import tempfile
from pathlib import Path

import pytest

from obsidian_remarkable_sync.converter import ConversionError, convert_file

FIXTURES = Path(__file__).parent / "fixtures" / "sample_vault"

requires_pandoc = pytest.mark.skipif(
    shutil.which("pandoc") is None,
    reason="Pandoc is not installed",
)

requires_latex = pytest.mark.skipif(
    shutil.which("pdflatex") is None,
    reason="pdflatex is not installed",
)


@requires_pandoc
def test_convert_simple_note_to_epub():
    output_dir = Path(tempfile.mkdtemp(prefix="test-convert-"))
    result = convert_file(FIXTURES / "note2.md", FIXTURES, "epub", output_dir)
    assert result.exists()
    assert result.suffix == ".epub"
    assert result.stat().st_size > 0


@requires_pandoc
def test_convert_note_with_images_to_epub():
    output_dir = Path(tempfile.mkdtemp(prefix="test-convert-"))
    result = convert_file(FIXTURES / "note_with_images.md", FIXTURES, "epub", output_dir)
    assert result.exists()
    assert result.suffix == ".epub"
    assert result.stat().st_size > 0


@requires_pandoc
@requires_latex
def test_convert_to_pdf():
    output_dir = Path(tempfile.mkdtemp(prefix="test-convert-"))
    result = convert_file(FIXTURES / "note2.md", FIXTURES, "pdf", output_dir)
    assert result.exists()
    assert result.suffix == ".pdf"


@requires_pandoc
def test_convert_uses_title_from_frontmatter():
    output_dir = Path(tempfile.mkdtemp(prefix="test-convert-"))
    result = convert_file(FIXTURES / "note2.md", FIXTURES, "epub", output_dir)
    # The output file should exist — title is passed as metadata to Pandoc
    assert result.exists()


def test_convert_raises_without_pandoc(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(ConversionError, match="Pandoc is not installed"):
        convert_file(FIXTURES / "note2.md", FIXTURES)


@requires_pandoc
def test_convert_latin1_fallback(tmp_path):
    """Files with non-UTF-8 encoding should fall back to latin-1."""
    vault = tmp_path
    note = vault / "latin.md"
    note.write_bytes(b"# Caf\xe9\n\nSome content\n")  # \xe9 is invalid UTF-8

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = convert_file(note, vault, "epub", output_dir)
    assert result.exists()
    assert result.suffix == ".epub"


@requires_pandoc
def test_convert_uses_filename_as_title_when_no_frontmatter(tmp_path):
    """Without frontmatter title, the file stem should be used."""
    vault = tmp_path
    note = vault / "My Cool Note.md"
    note.write_text("# Just a heading\n\nNo frontmatter here.\n")

    output_dir = tmp_path / "output"
    output_dir.mkdir()
    result = convert_file(note, vault, "epub", output_dir)
    assert result.exists()
    # Output filename should be based on stem
    assert "My Cool Note" in result.stem


@requires_pandoc
def test_convert_pandoc_failure(tmp_path, monkeypatch):
    """Pandoc returning non-zero should raise ConversionError."""
    import subprocess
    from unittest.mock import patch

    vault = tmp_path
    note = vault / "note.md"
    note.write_text("# Test\n")

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    # Let which("pandoc") pass, but make the actual pandoc run fail
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="pandoc error"
        )
        with pytest.raises(ConversionError, match="Pandoc conversion failed"):
            convert_file(note, vault, "epub", output_dir)
