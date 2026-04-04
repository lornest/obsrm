"""Tests for Obsidian markdown preprocessing."""

from pathlib import Path

from obsidian_remarkable_sync.markdown_processor import process_markdown

FIXTURES = Path(__file__).parent / "fixtures" / "sample_vault"


def test_extract_frontmatter_title():
    content = "---\ntitle: My Note\ntags: [a]\n---\n\n# Body\n"
    processed, title, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert title == "My Note"
    assert "---" not in processed
    assert "# Body" in processed


def test_no_frontmatter():
    content = "# Just a heading\n\nSome text.\n"
    processed, title, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert title is None
    assert processed == content


def test_strip_dataview_blocks():
    content = 'Before\n\n```dataview\nTABLE file.mtime\nFROM "/"\n```\n\nAfter\n'
    processed, _, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "dataview" not in processed
    assert "Before" in processed
    assert "After" in processed


def test_strip_dataviewjs_blocks():
    content = "Before\n\n```dataviewjs\ndv.table(...)\n```\n\nAfter\n"
    processed, _, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "dataviewjs" not in processed


def test_resolve_embed():
    content = "Before\n\n![[note2]]\n\nAfter\n"
    processed, _, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "content of note2" in processed
    assert "![[note2]]" not in processed


def test_resolve_embed_strips_embedded_frontmatter():
    content = "![[note2]]\n"
    processed, _, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "title:" not in processed
    assert "---" not in processed


def test_circular_embed_detected():
    content = "![[self]]\n"
    processed, _, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "Could not resolve" in processed


def test_image_embed_nonexistent():
    content = "![[photo.png]]\n"
    processed, _, images = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    # Image doesn't exist in fixture, so it stays unchanged
    assert "![[photo.png]]" in processed
    assert len(images) == 0


def test_heading_embed():
    content = "![[note2#Details]]\n"
    processed, _, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "detailed content" in processed


# --- Image handling tests ---


def test_obsidian_image_embed_resolved():
    content = "![[test-image.png]]\n"
    processed, _, images = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "![](test-image.png)" in processed
    assert len(images) == 1
    assert images[0][0] == FIXTURES / "attachments" / "test-image.png"
    assert images[0][1] == "test-image.png"


def test_obsidian_image_embed_with_size():
    content = "![[test-image.png|300]]\n"
    processed, _, images = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "![300](test-image.png)" in processed
    assert len(images) == 1


def test_standard_image_resolved():
    content = "![Alt text](attachments/test-image.png)\n"
    processed, _, images = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "![Alt text](test-image.png)" in processed
    assert len(images) == 1
    assert images[0][0] == FIXTURES / "attachments" / "test-image.png"


def test_external_image_not_resolved():
    content = "![External](https://example.com/image.png)\n"
    processed, _, images = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "https://example.com/image.png" in processed
    assert len(images) == 0


def test_full_note_with_images():
    """Test processing the fixture note that has various image types."""
    content = (FIXTURES / "note_with_images.md").read_text()
    processed, title, images = process_markdown(content, FIXTURES / "note_with_images.md", FIXTURES)
    assert title == "Note With Images"
    # Two Obsidian embeds + one standard image = 3 resolved images
    assert len(images) == 3
    # External image should not be in the list
    assert all(img[0].name == "test-image.png" for img in images)
    # External URL preserved
    assert "https://example.com/image.png" in processed


# --- Branch coverage for _resolve_embeds ---


def test_recursion_limit():
    """Embeds nested deeper than 10 levels stop resolving."""
    from obsidian_remarkable_sync.markdown_processor import _resolve_embeds

    # depth=11 should return content unchanged
    content = "![[note2]]\n"
    result = _resolve_embeds(content, FIXTURES, seen=set(), depth=11)
    assert result == content


def test_circular_embed_actual_cycle(tmp_path):
    """Two files that embed each other should hit the circular embed guard."""
    (tmp_path / "a.md").write_text("before\n![[b]]\nafter\n")
    (tmp_path / "b.md").write_text("hello\n![[a]]\ngoodbye\n")
    processed, _, _ = process_markdown((tmp_path / "a.md").read_text(), tmp_path / "a.md", tmp_path)
    assert "Circular embed" in processed
    assert "hello" in processed


def test_embed_encoding_error(tmp_path):
    """A file that can't be read as UTF-8 should produce an error message."""
    bad = tmp_path / "bad.md"
    bad.write_bytes(b"\x80\x81\x82")
    content = "![[bad]]\n"
    processed, _, _ = process_markdown(content, tmp_path / "main.md", tmp_path)
    assert "encoding error" in processed


def test_heading_embed_missing_section():
    """Referencing a heading that doesn't exist shows a 'not found' message."""
    content = "![[note2#NonexistentSection]]\n"
    processed, _, _ = process_markdown(content, FIXTURES / "note1.md", FIXTURES)
    assert "Section not found" in processed


def test_ambiguous_filename_resolution(tmp_path):
    """When multiple files match, the shortest path (closest to root) wins."""
    (tmp_path / "target.md").write_text("root version\n")
    sub = tmp_path / "a" / "b"
    sub.mkdir(parents=True)
    (sub / "target.md").write_text("deep version\n")

    content = "![[target]]\n"
    processed, _, _ = process_markdown(content, tmp_path / "main.md", tmp_path)
    assert "root version" in processed
