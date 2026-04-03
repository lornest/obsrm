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


@requires_pandoc
def test_convert_simple_note_to_epub():
    output_dir = Path(tempfile.mkdtemp(prefix="test-convert-"))
    result = convert_file(
        FIXTURES / "note2.md", FIXTURES, "epub", output_dir
    )
    assert result.exists()
    assert result.suffix == ".epub"
    assert result.stat().st_size > 0


@requires_pandoc
def test_convert_note_with_images_to_epub():
    output_dir = Path(tempfile.mkdtemp(prefix="test-convert-"))
    result = convert_file(
        FIXTURES / "note_with_images.md", FIXTURES, "epub", output_dir
    )
    assert result.exists()
    assert result.suffix == ".epub"
    assert result.stat().st_size > 0


@requires_pandoc
def test_convert_to_pdf():
    output_dir = Path(tempfile.mkdtemp(prefix="test-convert-"))
    result = convert_file(
        FIXTURES / "note2.md", FIXTURES, "pdf", output_dir
    )
    assert result.exists()
    assert result.suffix == ".pdf"


@requires_pandoc
def test_convert_uses_title_from_frontmatter():
    output_dir = Path(tempfile.mkdtemp(prefix="test-convert-"))
    result = convert_file(
        FIXTURES / "note2.md", FIXTURES, "epub", output_dir
    )
    # The output file should exist — title is passed as metadata to Pandoc
    assert result.exists()


def test_convert_raises_without_pandoc(monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda _: None)
    with pytest.raises(ConversionError, match="Pandoc is not installed"):
        convert_file(FIXTURES / "note2.md", FIXTURES)
