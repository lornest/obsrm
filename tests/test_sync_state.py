"""Tests for sync state management."""

import contextlib
import tempfile
from pathlib import Path
from unittest.mock import patch

from obsrm.sync_state import SyncState


def test_empty_state():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        state_path = Path(f.name)

    # Remove the file to simulate no prior state
    state_path.unlink()

    state = SyncState(state_path)
    assert state.entries == {}


def test_save_and_load():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        state_path = Path(f.name)

    state = SyncState(state_path)
    state.update_entry("note.md", "abc123", "/Obsidian/note")
    state.save()

    # Reload
    state2 = SyncState(state_path)
    assert "note.md" in state2.entries
    assert state2.entries["note.md"].content_hash == "abc123"
    assert state2.entries["note.md"].remote_path == "/Obsidian/note"

    state_path.unlink()


def test_changeset_added():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        state_path = Path(f.name)
    state_path.unlink()

    state = SyncState(state_path)
    changeset = state.compute_changeset({"note.md": "abc123"})
    assert changeset.added == ["note.md"]
    assert changeset.modified == []
    assert changeset.deleted == []

    state_path.unlink(missing_ok=True)


def test_changeset_modified():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        state_path = Path(f.name)

    state = SyncState(state_path)
    state.update_entry("note.md", "old_hash", "/Obsidian/note")
    state.save()

    state2 = SyncState(state_path)
    changeset = state2.compute_changeset({"note.md": "new_hash"})
    assert changeset.added == []
    assert changeset.modified == ["note.md"]

    state_path.unlink()


def test_changeset_deleted():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        state_path = Path(f.name)

    state = SyncState(state_path)
    state.update_entry("note.md", "abc123", "/Obsidian/note")
    state.save()

    state2 = SyncState(state_path)
    # Empty current files, with delete_removed=True
    changeset = state2.compute_changeset({}, delete_removed=True)
    assert changeset.deleted == ["note.md"]

    # Without delete_removed, deleted list is empty
    changeset2 = state2.compute_changeset({}, delete_removed=False)
    assert changeset2.deleted == []

    state_path.unlink()


def test_changeset_no_changes():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        state_path = Path(f.name)

    state = SyncState(state_path)
    state.update_entry("note.md", "abc123", "/Obsidian/note")
    state.save()

    state2 = SyncState(state_path)
    changeset = state2.compute_changeset({"note.md": "abc123"})
    assert not changeset.has_changes
    assert changeset.summary() == "no changes"

    state_path.unlink()


def test_remove_entry():
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as f:
        state_path = Path(f.name)

    state = SyncState(state_path)
    state.update_entry("note.md", "abc123", "/Obsidian/note")
    state.remove_entry("note.md")
    assert "note.md" not in state.entries

    # Removing non-existent key is safe
    state.remove_entry("nonexistent.md")

    state_path.unlink(missing_ok=True)


def test_deletion_sync_end_to_end(tmp_path):
    """Full deletion sync flow: sync files, remove one, re-sync, verify deletion."""
    state_path = tmp_path / ".sync-state.json"

    # Step 1: Initial sync — two files
    state = SyncState(state_path)
    files_v1 = {"note1.md": "hash1", "note2.md": "hash2"}
    changeset = state.compute_changeset(files_v1, delete_removed=True)
    assert changeset.added == ["note1.md", "note2.md"]
    assert changeset.deleted == []

    # Simulate successful upload
    state.update_entry("note1.md", "hash1", "/Obsidian/note1")
    state.update_entry("note2.md", "hash2", "/Obsidian/note2")
    state.save()

    # Step 2: Remove note2 from vault, modify note1
    files_v2 = {"note1.md": "hash1_updated"}
    state2 = SyncState(state_path)
    changeset2 = state2.compute_changeset(files_v2, delete_removed=True)
    assert changeset2.modified == ["note1.md"]
    assert changeset2.deleted == ["note2.md"]
    assert changeset2.added == []

    # Verify we can get the remote path for deletion
    assert state2.entries["note2.md"].remote_path == "/Obsidian/note2"

    # Simulate successful operations
    state2.update_entry("note1.md", "hash1_updated", "/Obsidian/note1")
    state2.remove_entry("note2.md")
    state2.save()

    # Step 3: Verify final state
    state3 = SyncState(state_path)
    assert "note1.md" in state3.entries
    assert "note2.md" not in state3.entries
    assert state3.entries["note1.md"].content_hash == "hash1_updated"

    # No more changes
    changeset3 = state3.compute_changeset(files_v2, delete_removed=True)
    assert not changeset3.has_changes


def test_deletion_sync_disabled_by_default(tmp_path):
    """When delete_removed=False (default), removed files are not flagged."""
    state_path = tmp_path / ".sync-state.json"

    state = SyncState(state_path)
    state.update_entry("note.md", "hash1", "/Obsidian/note")
    state.save()

    state2 = SyncState(state_path)
    changeset = state2.compute_changeset({}, delete_removed=False)
    assert changeset.deleted == []
    assert not changeset.has_changes


def test_save_is_atomic_on_write_failure(tmp_path):
    """If save() fails mid-write, the original state file is preserved."""
    state_path = tmp_path / ".sync-state.json"

    # Write initial valid state
    state = SyncState(state_path)
    state.update_entry("note.md", "hash1", "/Obsidian/note")
    state.save()

    original_content = state_path.read_text()

    # Now simulate a write failure during the temp file phase
    state.update_entry("note2.md", "hash2", "/Obsidian/note2")
    with (
        patch("obsrm.sync_state.json.dump", side_effect=OSError("disk full")),
        contextlib.suppress(OSError),
    ):
        state.save()

    # Original file should be intact
    assert state_path.read_text() == original_content
    reloaded = SyncState(state_path)
    assert "note.md" in reloaded.entries
    assert "note2.md" not in reloaded.entries

    # No leftover temp files
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert tmp_files == []
