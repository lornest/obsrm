"""Tests for Obsidian markdown preprocessing."""

from pathlib import Path

from obsidian_remarkable_sync.markdown_processor import process_markdown

FIXTURES = Path(__file__).parent / "fixtures" / "sample_vault"


def test_extract_frontmatter_title():
    content = "---\ntitle: My Note\ntags: [a]\n---\n\n# Body\n"
    processed, title, _ = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert title == "My Note"
    assert "---" not in processed
    assert "# Body" in processed


def test_no_frontmatter():
    content = "# Just a heading\n\nSome text.\n"
    processed, title, _ = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert title is None
    assert processed == content


def test_strip_dataview_blocks():
    content = "Before\n\n```dataview\nTABLE file.mtime\nFROM \"/\"\n```\n\nAfter\n"
    processed, _, _ = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "dataview" not in processed
    assert "Before" in processed
    assert "After" in processed


def test_strip_dataviewjs_blocks():
    content = "Before\n\n```dataviewjs\ndv.table(...)\n```\n\nAfter\n"
    processed, _, _ = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "dataviewjs" not in processed


def test_resolve_embed():
    content = "Before\n\n![[note2]]\n\nAfter\n"
    processed, _, _ = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "content of note2" in processed
    assert "![[note2]]" not in processed


def test_resolve_embed_strips_embedded_frontmatter():
    content = "![[note2]]\n"
    processed, _, _ = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "title:" not in processed
    assert "---" not in processed


def test_circular_embed_detected():
    content = "![[self]]\n"
    processed, _, _ = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "Could not resolve" in processed


def test_image_embed_nonexistent():
    content = "![[photo.png]]\n"
    processed, _, images = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    # Image doesn't exist in fixture, so it stays unchanged
    assert "![[photo.png]]" in processed
    assert len(images) == 0


def test_heading_embed():
    content = "![[note2#Details]]\n"
    processed, _, _ = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "detailed content" in processed


# --- Image handling tests ---


def test_obsidian_image_embed_resolved():
    content = "![[test-image.png]]\n"
    processed, _, images = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "![](test-image.png)" in processed
    assert len(images) == 1
    assert images[0][0] == FIXTURES / "attachments" / "test-image.png"
    assert images[0][1] == "test-image.png"


def test_obsidian_image_embed_with_size():
    content = "![[test-image.png|300]]\n"
    processed, _, images = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "![300](test-image.png)" in processed
    assert len(images) == 1


def test_standard_image_resolved():
    content = "![Alt text](attachments/test-image.png)\n"
    processed, _, images = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "![Alt text](test-image.png)" in processed
    assert len(images) == 1
    assert images[0][0] == FIXTURES / "attachments" / "test-image.png"


def test_external_image_not_resolved():
    content = "![External](https://example.com/image.png)\n"
    processed, _, images = process_markdown(
        content, FIXTURES / "note1.md", FIXTURES
    )
    assert "https://example.com/image.png" in processed
    assert len(images) == 0


def test_full_note_with_images():
    """Test processing the fixture note that has various image types."""
    content = (FIXTURES / "note_with_images.md").read_text()
    processed, title, images = process_markdown(
        content, FIXTURES / "note_with_images.md", FIXTURES
    )
    assert title == "Note With Images"
    # Two Obsidian embeds + one standard image = 3 resolved images
    assert len(images) == 3
    # External image should not be in the list
    assert all(img[0].name == "test-image.png" for img in images)
    # External URL preserved
    assert "https://example.com/image.png" in processed
