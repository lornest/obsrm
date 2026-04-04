"""Tests for rm_extract notebook text extraction."""

import io
import zipfile
from unittest.mock import MagicMock, patch

import pytest

rmscene = pytest.importorskip("rmscene", reason="rmscene not installed")

from obsidian_remarkable_sync.rm_extract import (  # noqa: E402
    _extract_page_text,
    extract_text_from_rmdoc,
    pages_to_markdown,
)

# --- pages_to_markdown ---


def test_pages_to_markdown_single_page():
    pages = [{"paragraphs": [{"text": "Hello world", "style": "plain"}]}]
    result = pages_to_markdown(pages, "MyNote")
    assert "Hello world" in result
    assert "---" not in result  # no separator for single page


def test_pages_to_markdown_multiple_pages():
    pages = [
        {"paragraphs": [{"text": "Page one", "style": "plain"}]},
        {"paragraphs": [{"text": "Page two", "style": "plain"}]},
    ]
    result = pages_to_markdown(pages, "MyNote")
    assert "Page one" in result
    assert "---" in result  # separator between pages
    assert "Page two" in result


def test_pages_to_markdown_heading_style():
    pages = [{"paragraphs": [{"text": "My Title", "style": "heading"}]}]
    result = pages_to_markdown(pages, "MyNote")
    assert "# My Title" in result


def test_pages_to_markdown_bold_style():
    pages = [{"paragraphs": [{"text": "Important", "style": "bold"}]}]
    result = pages_to_markdown(pages, "MyNote")
    assert "**Important**" in result


def test_pages_to_markdown_mixed_styles():
    pages = [
        {
            "paragraphs": [
                {"text": "Title", "style": "heading"},
                {"text": "Normal text", "style": "plain"},
                {"text": "Bold text", "style": "bold"},
            ]
        }
    ]
    result = pages_to_markdown(pages, "MyNote")
    assert "# Title" in result
    assert "Normal text" in result
    assert "**Bold text**" in result


def test_pages_to_markdown_empty_pages():
    result = pages_to_markdown([], "MyNote")
    assert result == ""


def test_pages_to_markdown_default_style():
    """Paragraph with no style key defaults to plain."""
    pages = [{"paragraphs": [{"text": "no style key"}]}]
    result = pages_to_markdown(pages, "MyNote")
    assert "no style key" in result
    assert "# " not in result
    assert "**" not in result


# --- _extract_page_text ---


def test_extract_page_text_returns_empty_on_read_error():
    """Malformed .rm data should return empty paragraphs, not raise."""
    f = io.BytesIO(b"this is not a valid .rm file")
    result = _extract_page_text(f)
    assert result == {"paragraphs": []}


def test_extract_page_text_skips_non_text_blocks():
    """Blocks that aren't RootTextBlock should be ignored."""
    mock_block = MagicMock()
    mock_block.__class__.__name__ = "SomeOtherBlock"

    with patch("rmscene.read_blocks", return_value=[mock_block]):
        f = io.BytesIO(b"")
        result = _extract_page_text(f)
        assert result == {"paragraphs": []}


def test_extract_page_text_skips_none_value_blocks():
    """RootTextBlock with value=None should be skipped."""
    from rmscene import RootTextBlock

    mock_block = MagicMock(spec=RootTextBlock)
    mock_block.value = None

    with patch("rmscene.read_blocks", return_value=[mock_block]):
        f = io.BytesIO(b"")
        result = _extract_page_text(f)
        assert result == {"paragraphs": []}


def _make_text_block(text, styles=None):
    """Create a mock RootTextBlock with the given text content."""
    from rmscene import RootTextBlock

    mock_block = MagicMock()
    # Make isinstance check work
    mock_block.__class__ = RootTextBlock

    mock_item = MagicMock()
    mock_item.value = text
    mock_block.value.items.sequence_items.return_value = [mock_item]
    mock_block.value.styles = styles or {}
    return mock_block


def test_extract_page_text_skips_whitespace_only():
    """Blocks with only whitespace text should be skipped."""
    mock_block = _make_text_block("   \n  ")

    with patch("rmscene.read_blocks", return_value=[mock_block]):
        f = io.BytesIO(b"")
        result = _extract_page_text(f)
        assert result == {"paragraphs": []}


def test_extract_page_text_splits_paragraphs():
    """Multi-line text should be split into separate paragraphs."""
    mock_block = _make_text_block("Line one\nLine two\n\nLine three")

    with patch("rmscene.read_blocks", return_value=[mock_block]):
        f = io.BytesIO(b"")
        result = _extract_page_text(f)
        texts = [p["text"] for p in result["paragraphs"]]
        assert texts == ["Line one", "Line two", "Line three"]


def test_extract_page_text_heading_detection():
    """Heading style should be applied to the first paragraph only."""
    from rmscene.scene_items import ParagraphStyle

    mock_block = _make_text_block(
        "Title\nBody text",
        styles={1: MagicMock(value=ParagraphStyle.HEADING)},
    )

    with patch("rmscene.read_blocks", return_value=[mock_block]):
        f = io.BytesIO(b"")
        result = _extract_page_text(f)
        assert result["paragraphs"][0]["style"] == "heading"
        assert result["paragraphs"][1]["style"] == "plain"


# --- extract_text_from_rmdoc ---


def test_extract_text_from_rmdoc_valid_zip(tmp_path):
    """A zip with .rm files should be processed."""
    rmdoc = tmp_path / "test.rmdoc"

    # Create a zip with a fake .rm file
    with zipfile.ZipFile(rmdoc, "w") as zf:
        zf.writestr("page1.rm", b"not real rm data")

    # The fake data will cause read_blocks to fail, returning empty pages
    result = extract_text_from_rmdoc(rmdoc)
    assert result == []  # no pages with paragraphs


def test_extract_text_from_rmdoc_no_rm_files(tmp_path):
    """A zip with no .rm files should return empty list."""
    rmdoc = tmp_path / "test.rmdoc"
    with zipfile.ZipFile(rmdoc, "w") as zf:
        zf.writestr("metadata.json", "{}")

    result = extract_text_from_rmdoc(rmdoc)
    assert result == []


def test_extract_text_from_rmdoc_sorts_pages(tmp_path):
    """Pages should be processed in sorted order."""
    rmdoc = tmp_path / "test.rmdoc"
    with zipfile.ZipFile(rmdoc, "w") as zf:
        zf.writestr("page_02.rm", b"data")
        zf.writestr("page_01.rm", b"data")
        zf.writestr("metadata.json", "{}")

    # Verify sorting by checking the files are processed in order
    calls = []
    original_extract = _extract_page_text

    def tracking_extract(f):
        calls.append(f.name)
        return original_extract(f)

    with patch(
        "obsidian_remarkable_sync.rm_extract._extract_page_text",
        side_effect=tracking_extract,
    ):
        extract_text_from_rmdoc(rmdoc)

    assert calls == ["page_01.rm", "page_02.rm"]
