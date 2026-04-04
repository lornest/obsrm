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
        "remarkable:\n"
        "  target_folder: /Test\n"
        "  format: epub\n"
        "sync:\n"
        "  delete_removed: true\n"
    )
    note = tmp_path / "note.md"
    note.write_text("# Hello\n")
    return tmp_path


@pytest.fixture
def runner():
    return CliRunner()


# --- sync ---


def test_sync_dry_run(runner, vault):
    result = runner.invoke(cli, ["sync", "--vault-path", str(vault), "--dry-run"])
    assert result.exit_code == 0
    assert "dry run" in result.output


def test_sync_nothing_to_sync(runner, vault):
    # Pre-populate state so there are no changes
    state = SyncState(vault / ".sync-state.json")
    # Need to match the actual hash
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

    mock_client_cls.return_value = MagicMock()
    mock_convert.side_effect = ConversionError("pandoc broke")

    result = runner.invoke(cli, ["sync", "--vault-path", str(vault)])
    assert result.exit_code == 1
    assert "Conversion failed" in result.output


@patch("obsidian_remarkable_sync.cli.RemarkableClient")
@patch("obsidian_remarkable_sync.cli.convert_file")
def test_sync_upload_failure_continues(mock_convert, mock_client_cls, runner, vault):
    mock_client = MagicMock()
    mock_client_cls.return_value = mock_client
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
