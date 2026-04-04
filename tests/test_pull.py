"""Tests for reverse sync (pull from reMarkable)."""

from pathlib import Path
from unittest.mock import MagicMock

from obsidian_remarkable_sync.pull import (
    _append_annotation_link,
    _handle_pdf,
    list_remote_files,
    remote_path_to_vault_rel,
)
from obsidian_remarkable_sync.sync_state import SyncState

# --- remote_path_to_vault_rel ---


def test_remote_path_to_vault_rel():
    assert remote_path_to_vault_rel("/Obsidian/GL/Notes", "/Obsidian") == "GL/Notes"


def test_remote_path_to_vault_rel_root_file():
    assert remote_path_to_vault_rel("/Obsidian/MyNote", "/Obsidian") == "MyNote"


def test_remote_path_to_vault_rel_nested():
    assert remote_path_to_vault_rel("/Obsidian/A/B/C/File", "/Obsidian") == "A/B/C/File"


# --- list_remote_files ---


def test_list_remote_files_filters_directories():
    client = MagicMock()
    client.list_recursive.return_value = {
        "/Obsidian/Notes": "d",
        "/Obsidian/Notes/File1": "f",
        "/Obsidian/Notes/File2": "f",
        "/Obsidian/Notes/Sub": "d",
        "/Obsidian/Notes/Sub/File3": "f",
    }
    result = list_remote_files(client, "/Obsidian")
    assert "/Obsidian/Notes/File1" in result
    assert "/Obsidian/Notes/File2" in result
    assert "/Obsidian/Notes/Sub/File3" in result
    assert "/Obsidian/Notes" not in result
    assert "/Obsidian/Notes/Sub" not in result


# --- _create_markdown ---


def test_handle_pdf_creates_markdown(tmp_path):
    # Create a fake PDF
    pdf_file = tmp_path / "source.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 fake")

    md_path = tmp_path / "vault" / "Test.md"
    vault_path = tmp_path / "vault"
    vault_path.mkdir()

    md, att = _handle_pdf(pdf_file, md_path, "Test", vault_path, Path("."), "attachments")

    content = md.read_text()
    assert "# Test" in content
    assert "![[attachments/Test.pdf]]" in content
    assert att.exists()


# --- _append_annotation_link ---


def test_append_annotation_to_existing_file(tmp_path):
    md_path = tmp_path / "Existing.md"
    md_path.write_text("# Existing Note\n\nSome content here.\n")

    pdf_rel = Path("attachments/Existing.pdf")
    _append_annotation_link(md_path, pdf_rel)

    content = md_path.read_text()
    assert "# Existing Note" in content
    assert "Some content here." in content
    assert "## Annotations" in content
    assert "![[attachments/Existing.pdf]]" in content


def test_append_annotation_idempotent(tmp_path):
    md_path = tmp_path / "Note.md"
    md_path.write_text("# Note\n\nContent.\n")

    pdf_rel = Path("attachments/Note.pdf")
    _append_annotation_link(md_path, pdf_rel)
    _append_annotation_link(md_path, pdf_rel)

    content = md_path.read_text()
    assert content.count("![[attachments/Note.pdf]]") == 1


def test_append_annotation_updates_existing_section(tmp_path):
    md_path = tmp_path / "Note.md"
    md_path.write_text("# Note\n\nContent.\n\n## Annotations\n\n![[attachments/old.pdf]]\n")

    pdf_rel = Path("attachments/new.pdf")
    _append_annotation_link(md_path, pdf_rel)

    content = md_path.read_text()
    assert "![[attachments/new.pdf]]" in content


# --- Pull uses sync state to detect new files ---


def test_pull_skips_already_pushed_files(tmp_path):
    """Files in sync state should not be detected as new by pull."""
    state_path = tmp_path / ".sync-state.json"
    state = SyncState(state_path)
    state.update_entry("Notes/File1.md", "hash1", "/Obsidian/Notes/File1")
    state.save()

    state2 = SyncState(state_path)
    known = state2.known_remote_paths()

    remote_files = {
        "/Obsidian/Notes/File1": "f",  # already pushed
        "/Obsidian/Notes/NewFile": "f",  # new on reMarkable
    }

    new_files = [p for p in remote_files if p not in known]
    assert new_files == ["/Obsidian/Notes/NewFile"]


def test_pull_detects_all_when_no_state(tmp_path):
    """With no sync state, all remote files are new."""
    state_path = tmp_path / ".sync-state.json"
    state = SyncState(state_path)
    known = state.known_remote_paths()

    remote_files = {
        "/Obsidian/File1": "f",
        "/Obsidian/File2": "f",
    }

    new_files = [p for p in remote_files if p not in known]
    assert len(new_files) == 2
