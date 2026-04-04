"""Tests for CLI commands with mocked RemarkableClient and convert_file."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from obsidian_remarkable_sync.cli import cli
from obsidian_remarkable_sync.remarkable import RmapiError
from obsidian_remarkable_sync.sync_state import SyncState


@pytest.fixture
def vault(tmp_path):
    """Create a minimal vault with config and a markdown file."""
    config = tmp_path / "sync-config.yaml"
    config.write_text(
        "remarkable:\n  target_folder: /Test\n  format: epub\nsync:\n  delete_removed: true\n"
    )
    note = tmp_path / "note.md"
    note.write_text("# Hello\n")
    return tmp_path


@pytest.fixture
def runner():
    return CliRunner()


# --- sync ---


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_sync_dry_run(mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {}

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault), "--dry-run"])
    assert result.exit_code == 0
    assert "dry run" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_sync_nothing_to_sync(mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/note": "f"}
    mock_client.stat.return_value = {"ModifiedClient": "2026-04-04T13:00:00Z"}

    # Pre-populate state so there are no changes
    state = SyncState(vault / ".sync-state.json")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.save()

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 0
    assert "Nothing to sync" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
@patch("obsidian_remarkable_sync.cli.convert_file")
def test_sync_uploads_new_file(mock_convert, mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {}
    fake_epub = vault / "note.epub"
    fake_epub.write_bytes(b"fake epub")
    mock_convert.return_value = fake_epub

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 0
    mock_client.upload.assert_called_once()
    # State should be saved
    state = SyncState(vault / ".sync-state.json")
    assert "note.md" in state.entries


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
@patch("obsidian_remarkable_sync.cli.convert_file")
def test_sync_conversion_failure_continues(mock_convert, mock_client_cls, runner, vault):
    from obsidian_remarkable_sync.converter import ConversionError

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {}
    mock_convert.side_effect = ConversionError("pandoc broke")

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 1
    assert "Conversion failed" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
@patch("obsidian_remarkable_sync.cli.convert_file")
def test_sync_upload_failure_continues(mock_convert, mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {}
    mock_client.upload.side_effect = RmapiError("network error")
    fake_epub = vault / "note.epub"
    fake_epub.write_bytes(b"fake epub")
    mock_convert.return_value = fake_epub

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 1
    assert "Upload failed" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_sync_delete_failure_preserves_state(mock_client_cls, runner, vault):
    """When remote delete fails, the entry must NOT be removed from state."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.delete.side_effect = RmapiError("delete failed")
    # File still on reMarkable since delete failed
    mock_client.list_recursive.return_value = {"/Test/note": "f", "/Test/gone": "f"}

    # Remove the file from vault but keep it in state
    state = SyncState(vault / ".sync-state.json")
    state.update_entry("gone.md", "oldhash", "/Test/gone")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.save()

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 1
    assert "Delete failed" in result.output

    # Entry should still be in state since remote delete failed
    state2 = SyncState(vault / ".sync-state.json")
    assert "gone.md" in state2.entries


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_sync_delete_success_removes_state(mock_client_cls, runner, vault):
    """When remote delete succeeds, the entry must be removed from state."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.is_folder_empty.return_value = False
    mock_client.list_recursive.return_value = {"/Test/note": "f"}

    state = SyncState(vault / ".sync-state.json")
    state.update_entry("gone.md", "oldhash", "/Test/gone")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.save()

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 0

    state2 = SyncState(vault / ".sync-state.json")
    assert "gone.md" not in state2.entries


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_sync_rmapi_not_found(mock_client_cls, runner, vault):
    mock_client_cls.side_effect = RmapiError("rmapi is not installed")
    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 1
    assert "rmapi" in result.output


# --- status ---


def test_status_no_state(runner, vault):
    result = runner.invoke(cli, ["status", "--vault-path", str(vault)])
    assert result.exit_code == 0
    assert "No files have been synced" in result.output


def test_status_with_entries(runner, vault):
    state = SyncState(vault / ".sync-state.json")
    state.update_entry("note.md", "abc", "/Test/note")
    state.save()

    result = runner.invoke(cli, ["status", "--vault-path", str(vault)])
    assert result.exit_code == 0
    assert "note.md" in result.output
    assert "/Test/note" in result.output


# --- pull ---


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_dry_run(mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {
        "/Test/NewNote": "f",
        "/Test/Sub": "d",
    }

    result = runner.invoke(cli, ["pull", "--vault-path", str(vault), "--dry-run"])
    assert result.exit_code == 0
    assert "dry run" in result.output
    assert "NewNote" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_nothing_to_pull(mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/note": "f"}
    mock_client.stat.return_value = {"ModifiedClient": "2026-04-04T13:00:00Z"}

    # Pre-track the remote path in state with matching timestamp
    state = SyncState(vault / ".sync-state.json")
    state.update_entry("note.md", "abc", "/Test/note", "2026-04-04T13:00:00Z")
    state.save()

    result = runner.invoke(cli, ["pull", "--vault-path", str(vault)])
    assert result.exit_code == 0
    assert "Nothing to pull" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_downloads_new_file(mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/NewNote": "f"}
    mock_client.stat.return_value = {"ModifiedClient": "2026-04-04T13:00:00Z"}

    # Mock pull_file to create the md file
    def fake_pull_file(client, remote_path, vault_path, target_folder, att_folder):
        md = vault_path / "NewNote.md"
        md.write_text("# NewNote\n\n![[attachments/NewNote.pdf]]\n")
        return md, vault_path / "attachments" / "NewNote.pdf"

    with patch("obsidian_remarkable_sync.pull.pull_file", side_effect=fake_pull_file):
        result = runner.invoke(cli, ["pull", "--vault-path", str(vault)])

    assert result.exit_code == 0
    assert "Pull complete: 1" in result.output

    # State should be updated
    state = SyncState(vault / ".sync-state.json")
    assert "NewNote.md" in state.entries
    assert state.entries["NewNote.md"].remote_path == "/Test/NewNote"


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_failure_continues(mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/Bad": "f"}

    with patch(
        "obsidian_remarkable_sync.pull.pull_file", side_effect=RmapiError("download failed")
    ):
        result = runner.invoke(cli, ["pull", "--vault-path", str(vault)])

    assert result.exit_code == 1
    assert "Pull failed" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_rmapi_not_found(mock_client_cls, runner, vault):
    mock_client_cls.side_effect = RmapiError("rmapi is not installed")
    result = runner.invoke(cli, ["pull", "--vault-path", str(vault)])
    assert result.exit_code == 1
    assert "rmapi" in result.output


# --- deletion logic ---


def test_delete_pulled_file_removes_md_and_attachments(tmp_path):
    """_delete_pulled_file removes markdown and matching attachments."""
    from obsidian_remarkable_sync.cli import _delete_pulled_file
    from obsidian_remarkable_sync.sync_state import FileEntry

    vault = tmp_path
    (vault / "note.md").write_text("# Note\n")
    att_dir = vault / "attachments"
    att_dir.mkdir()
    (att_dir / "note.pdf").write_bytes(b"pdf")
    (att_dir / "note_p1.svg").write_bytes(b"svg1")
    (att_dir / "note_p2.svg").write_bytes(b"svg2")
    (att_dir / "other.pdf").write_bytes(b"keep")

    entry = FileEntry(
        rel_path="note.md", content_hash="abc", remote_path="/Test/note", origin="pull"
    )
    _delete_pulled_file(vault, entry, "attachments")

    assert not (vault / "note.md").exists()
    assert not (att_dir / "note.pdf").exists()
    assert not (att_dir / "note_p1.svg").exists()
    assert not (att_dir / "note_p2.svg").exists()
    assert (att_dir / "other.pdf").exists()


def test_delete_pulled_file_in_subdirectory(tmp_path):
    """_delete_pulled_file handles files in subdirectories."""
    from obsidian_remarkable_sync.cli import _delete_pulled_file
    from obsidian_remarkable_sync.sync_state import FileEntry

    vault = tmp_path
    sub = vault / "sub"
    sub.mkdir()
    (sub / "deep.md").write_text("# Deep\n")
    att_dir = vault / "attachments" / "sub"
    att_dir.mkdir(parents=True)
    (att_dir / "deep.pdf").write_bytes(b"pdf")

    entry = FileEntry(
        rel_path="sub/deep.md", content_hash="abc", remote_path="/Test/sub/deep", origin="pull"
    )
    _delete_pulled_file(vault, entry, "attachments")

    assert not (sub / "deep.md").exists()
    assert not (att_dir / "deep.pdf").exists()


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_deletes_pull_origin_removed_from_remarkable(mock_client_cls, runner, vault):
    """Pull-origin files deleted on reMarkable should be deleted locally."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    # Remote no longer has the pulled file
    mock_client.list_recursive.return_value = {"/Test/note": "f"}

    # Create pulled file locally
    pulled_md = vault / "pulled.md"
    pulled_md.write_text("# Pulled\n")

    state = SyncState(vault / ".sync-state.json")
    state.update_entry("pulled.md", "abc", "/Test/pulled", "2026-04-04T13:00:00Z", "pull")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.save()

    result = runner.invoke(cli, ["pull", "--vault-path", str(vault)])
    assert result.exit_code == 0
    assert "Deleted pulled.md" in result.output
    assert not pulled_md.exists()

    state2 = SyncState(vault / ".sync-state.json")
    assert "pulled.md" not in state2.entries


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_flags_push_origin_for_repush(mock_client_cls, runner, vault):
    """Push-origin files deleted on reMarkable are flagged for re-push."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {}

    state = SyncState(vault / ".sync-state.json")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.save()

    result = runner.invoke(cli, ["pull", "--vault-path", str(vault), "--dry-run"])
    assert result.exit_code == 0
    assert "re-push" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
@patch("obsidian_remarkable_sync.cli.convert_file")
def test_sync_repushes_push_origin_deleted_on_remarkable(
    mock_convert, mock_client_cls, runner, vault
):
    """Sync re-pushes files that were deleted on reMarkable but still exist locally."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    # Remote is empty — the pushed file was deleted on reMarkable
    mock_client.list_recursive.return_value = {}

    fake_epub = vault / "note.epub"
    fake_epub.write_bytes(b"fake epub")
    mock_convert.return_value = fake_epub

    # Pre-populate state as if file was previously pushed
    state = SyncState(vault / ".sync-state.json")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.save()

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 0
    assert "re-push" in result.output.lower() or "Re-pushing" in result.output

    # File should still be in state after re-push
    state2 = SyncState(vault / ".sync-state.json")
    assert "note.md" in state2.entries
    mock_client.upload.assert_called_once()


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_sync_pull_origin_deleted_locally_deletes_on_remarkable(mock_client_cls, runner, vault):
    """Pull-origin files deleted from Obsidian should be deleted from reMarkable."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/note": "f", "/Test/handwritten": "f"}
    mock_client.is_folder_empty.return_value = False

    state = SyncState(vault / ".sync-state.json")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    # Pull-origin file that no longer exists locally
    state.update_entry("handwritten.md", "abc", "/Test/handwritten", "2026-04-04T13:00:00Z", "pull")
    state.save()

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 0
    mock_client.delete.assert_called_once_with("/Test/handwritten")

    state2 = SyncState(vault / ".sync-state.json")
    assert "handwritten.md" not in state2.entries


# --- sync force ---


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
@patch("obsidian_remarkable_sync.cli.convert_file")
def test_sync_force_reuploads_all(mock_convert, mock_client_cls, runner, vault):
    """Force flag should re-upload all files, even those already tracked."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/note": "f"}
    fake_epub = vault / "note.epub"
    fake_epub.write_bytes(b"fake epub")
    mock_convert.return_value = fake_epub

    # Pre-track the file in state (no changes)
    state = SyncState(vault / ".sync-state.json")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.save()

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault), "--force"])
    assert result.exit_code == 0
    mock_client.upload.assert_called_once()


# --- sync modified files (replace path) ---


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
@patch("obsidian_remarkable_sync.cli.convert_file")
def test_sync_replaces_modified_file(mock_convert, mock_client_cls, runner, vault):
    """Modified files should be replaced, not uploaded as new."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/note": "f"}
    fake_epub = vault / "note.epub"
    fake_epub.write_bytes(b"fake epub")
    mock_convert.return_value = fake_epub

    # Track file with old hash
    state = SyncState(vault / ".sync-state.json")
    state.update_entry("note.md", "oldhash", "/Test/note")
    state.save()

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 0
    mock_client.replace.assert_called_once()
    mock_client.upload.assert_not_called()


# --- pull change detection ---


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_detects_changed_pull_origin_file(mock_client_cls, runner, vault):
    """Pull-origin files with changed ModifiedClient should be re-pulled."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/note": "f", "/Test/hw": "f"}
    mock_client.stat.return_value = {"ModifiedClient": "2026-04-04T15:00:00Z"}

    from obsidian_remarkable_sync.vault import _hash_file

    state = SyncState(vault / ".sync-state.json")
    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.update_entry("hw.md", "abc", "/Test/hw", "2026-04-04T13:00:00Z", "pull")
    state.save()

    result = runner.invoke(cli, ["pull", "--vault-path", str(vault), "--dry-run"])
    assert result.exit_code == 0
    assert "1 changed" in result.output
    assert "~ hw" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_stat_failure_skips_change_check(mock_client_cls, runner, vault):
    """If stat fails, the file should be skipped (not crash)."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/note": "f", "/Test/hw": "f"}
    mock_client.stat.side_effect = RmapiError("stat failed")

    from obsidian_remarkable_sync.vault import _hash_file

    state = SyncState(vault / ".sync-state.json")
    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.update_entry("hw.md", "abc", "/Test/hw", "2026-04-04T13:00:00Z", "pull")
    state.save()

    result = runner.invoke(cli, ["pull", "--vault-path", str(vault)])
    assert result.exit_code == 0


# --- re-push failure ---


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
@patch("obsidian_remarkable_sync.cli.convert_file")
def test_sync_repush_failure_reports_error(mock_convert, mock_client_cls, runner, vault):
    """Re-push failure should be reported but not crash."""
    from obsidian_remarkable_sync.converter import ConversionError

    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {}
    mock_convert.side_effect = ConversionError("pandoc broke")

    state = SyncState(vault / ".sync-state.json")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.save()

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 1
    assert "Re-push failed" in result.output


# --- vault path from env ---


def test_resolve_vault_path_from_env(runner, vault, monkeypatch):
    monkeypatch.setenv("VAULT_PATH", str(vault))
    # status command uses _resolve_vault_path and doesn't need rmapi
    result = runner.invoke(cli, ["status"])
    assert result.exit_code == 0
    assert str(vault) in result.output


# --- empty dir cleanup in _delete_pulled_file ---


def test_delete_pulled_file_cleans_nested_empty_dirs(tmp_path):
    """Empty parent directories should be cleaned up to the attachment root."""
    from obsidian_remarkable_sync.cli import _delete_pulled_file
    from obsidian_remarkable_sync.sync_state import FileEntry

    vault = tmp_path
    # Create deeply nested structure
    sub = vault / "a" / "b"
    sub.mkdir(parents=True)
    (sub / "deep.md").write_text("# Deep")
    att_dir = vault / "attachments" / "a" / "b"
    att_dir.mkdir(parents=True)
    (att_dir / "deep.pdf").write_bytes(b"pdf")

    entry = FileEntry(
        rel_path="a/b/deep.md", content_hash="abc", remote_path="/Test/a/b/deep", origin="pull"
    )
    _delete_pulled_file(vault, entry, "attachments")

    assert not (vault / "a" / "b").exists()
    assert not (vault / "a").exists()
    assert not (vault / "attachments" / "a" / "b").exists()
    assert not (vault / "attachments" / "a").exists()
    assert (vault / "attachments").exists()


# --- partial listing safety ---


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_skips_deletions_on_incomplete_listing(mock_client_cls, runner, vault):
    """If remote listing had errors, deletions should not be processed."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client

    # Simulate partial listing: list_recursive reports errors
    def fake_list_recursive(path, errors=None):
        if errors is not None:
            errors.append("/Test/FailedSubfolder")
        return {"/Test/note": "f"}

    mock_client.list_recursive.side_effect = fake_list_recursive
    mock_client.stat.return_value = {"ModifiedClient": "2026-04-04T13:00:00Z"}

    # Track a pull-origin file that won't appear in partial listing
    pulled_md = vault / "subfolder_note.md"
    pulled_md.write_text("# From subfolder\n")

    state = SyncState(vault / ".sync-state.json")
    from obsidian_remarkable_sync.vault import _hash_file

    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.update_entry(
        "subfolder_note.md", "abc", "/Test/FailedSubfolder/note", "2026-04-04T13:00:00Z", "pull"
    )
    state.save()

    result = runner.invoke(cli, ["pull", "--vault-path", str(vault)])
    assert result.exit_code == 0
    assert "Skipping deletion detection" in result.output
    # File should NOT be deleted
    assert pulled_md.exists()
    state2 = SyncState(vault / ".sync-state.json")
    assert "subfolder_note.md" in state2.entries


# --- end-to-end changed file pull ---


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
def test_pull_downloads_changed_pull_origin_file(mock_client_cls, runner, vault):
    """Pull-origin file with changed ModifiedClient should be re-downloaded."""
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
    mock_client.list_recursive.return_value = {"/Test/note": "f", "/Test/hw": "f"}
    mock_client.stat.return_value = {"ModifiedClient": "2026-04-04T15:00:00Z"}

    # Pre-create the pulled file
    hw_md = vault / "hw.md"
    hw_md.write_text("# Old content\n")

    from obsidian_remarkable_sync.vault import _hash_file

    state = SyncState(vault / ".sync-state.json")
    h = _hash_file(vault / "note.md")
    state.update_entry("note.md", h, "/Test/note")
    state.update_entry("hw.md", "abc", "/Test/hw", "2026-04-04T13:00:00Z", "pull")
    state.save()

    def fake_pull_file(client, remote_path, vault_path, target_folder, att_folder):
        md = vault_path / "hw.md"
        md.write_text("# Updated content from reMarkable\n")
        return md, None

    with patch("obsidian_remarkable_sync.pull.pull_file", side_effect=fake_pull_file):
        result = runner.invoke(cli, ["pull", "--vault-path", str(vault)])

    assert result.exit_code == 0
    assert "1 changed" in result.output
    assert "1 pulled" in result.output
    assert hw_md.read_text() == "# Updated content from reMarkable\n"

    state2 = SyncState(vault / ".sync-state.json")
    assert state2.entries["hw.md"].remote_modified == "2026-04-04T15:00:00Z"


# --- auth ---


def test_auth_rmapi_not_installed(runner, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    result = runner.invoke(cli, ["auth"])
    assert result.exit_code == 1
    assert "rmapi is not installed" in result.output


def test_auth_no_existing_config(runner, monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/rmapi")
    # Point home to tmp_path so .rmapi/rmapi.conf doesn't exist
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    with patch("subprocess.run") as mock_run:
        result = runner.invoke(cli, ["auth"])

    assert result.exit_code == 0
    assert "Starting rmapi authentication" in result.output
    mock_run.assert_called_once()


def test_auth_existing_config_decline(runner, monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/rmapi")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    rmapi_dir = tmp_path / ".rmapi"
    rmapi_dir.mkdir()
    (rmapi_dir / "rmapi.conf").write_text("token=abc123")

    result = runner.invoke(cli, ["auth"], input="n\n")
    assert result.exit_code == 0
    assert "Keeping existing authentication" in result.output
