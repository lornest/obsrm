"""Tests for RemarkableClient wrapper."""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

from obsidian_remarkable_sync.remarkable import RemarkableClient, RmapiError


@pytest.fixture
def client(monkeypatch):
    """Create a RemarkableClient with a fake rmapi binary."""
    monkeypatch.setattr("shutil.which", lambda _: "/usr/local/bin/rmapi")
    return RemarkableClient()


def test_init_raises_without_rmapi(monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: None)
    with pytest.raises(RmapiError, match="rmapi is not installed"):
        RemarkableClient()


def test_run_raises_on_nonzero_exit(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="some error"
        )
        with pytest.raises(RmapiError, match="some error"):
            client._run("ls", "/")


def test_run_returns_result_on_success(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="ok", stderr=""
        )
        result = client._run("ls", "/")
        assert result.stdout == "ok"


def test_delete_raises_on_failure(client):
    """delete() must propagate RmapiError so callers can keep state in sync."""
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="not found"
        )
        with pytest.raises(RmapiError):
            client.delete("/Obsidian/missing")


def test_delete_succeeds(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        client.delete("/Obsidian/note")  # should not raise


def test_list_folder(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[f]\tnote1\n[d]\tsubfolder\n", stderr=""
        )
        entries = client.list_folder("/Obsidian")
        assert len(entries) == 2


def test_list_folder_entries_parses_types(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[f]\tnote1\n[d]\tsubfolder\n", stderr=""
        )
        entries = client.list_folder_entries("/Obsidian")
        assert entries == [("f", "note1"), ("d", "subfolder")]


def test_list_folder_entries_skips_malformed_lines(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[f]\tnote1\nbad line\n[d]\tsub\n", stderr=""
        )
        entries = client.list_folder_entries("/")
        assert entries == [("f", "note1"), ("d", "sub")]


def test_is_folder_empty_true(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        assert client.is_folder_empty("/Obsidian/empty") is True


def test_is_folder_empty_false(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[f]\tfile\n", stderr=""
        )
        assert client.is_folder_empty("/Obsidian/notempty") is False


def test_is_folder_empty_returns_false_on_error(client):
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="err"
        )
        assert client.is_folder_empty("/bad") is False


def test_list_recursive(client):
    responses = [
        # First call: list /Root
        subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[f]\tnote1\n[d]\tsub\n", stderr=""
        ),
        # Second call: list /Root/sub
        subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[f]\tnote2\n", stderr=""
        ),
    ]
    with patch("subprocess.run", side_effect=responses):
        result = client.list_recursive("/Root")
        assert result == {
            "/Root/note1": "f",
            "/Root/sub": "d",
            "/Root/sub/note2": "f",
        }


def test_list_recursive_handles_subfolder_error(client):
    responses = [
        subprocess.CompletedProcess(
            args=[], returncode=0, stdout="[f]\tnote1\n[d]\tbad_sub\n", stderr=""
        ),
        # subfolder listing fails
        subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="permission denied"
        ),
    ]
    with patch("subprocess.run", side_effect=responses):
        result = client.list_recursive("/Root")
        assert "/Root/note1" in result
        assert "/Root/bad_sub" in result
        # no children from the failed subfolder


def test_upload_creates_folder_and_puts(client):
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with patch("subprocess.run", side_effect=fake_run):
        client.upload(Path("/tmp/note.epub"), "/Obsidian/sub")

    # Should have mkdir calls for /Obsidian and /Obsidian/sub, then put
    cmds = [c[1] for c in calls]  # second element is the rmapi subcommand
    assert "mkdir" in cmds
    assert "put" in cmds


def test_replace_delegates_to_upload(client):
    with patch.object(client, "upload") as mock_upload:
        client.replace(Path("/tmp/note.epub"), "/Obsidian/sub/note")
        mock_upload.assert_called_once_with(Path("/tmp/note.epub"), "/Obsidian/sub")


def test_replace_root_level(client):
    with patch.object(client, "upload") as mock_upload:
        client.replace(Path("/tmp/note.epub"), "/note")
        mock_upload.assert_called_once_with(Path("/tmp/note.epub"), "")


def test_download_tries_geta_then_get(client, tmp_path):
    pdf_file = tmp_path / "MyNote.pdf"
    pdf_file.write_bytes(b"%PDF")

    responses = [
        # geta fails
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""),
        # get succeeds
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    ]
    with patch("subprocess.run", side_effect=responses):
        result = client.download("/Obsidian/MyNote", tmp_path)
        assert result == pdf_file


def test_download_geta_success(client, tmp_path):
    pdf_file = tmp_path / "MyNote.pdf"
    pdf_file.write_bytes(b"%PDF")

    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )
        result = client.download("/Obsidian/MyNote", tmp_path)
        assert result == pdf_file


def test_download_raises_when_both_fail(client, tmp_path):
    responses = [
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr="fail"),
    ]
    with patch("subprocess.run", side_effect=responses), pytest.raises(RmapiError, match="failed"):
        client.download("/Obsidian/missing", tmp_path)


def test_download_raises_when_file_not_found(client, tmp_path):
    """get succeeds but no matching file found in output dir."""
    responses = [
        subprocess.CompletedProcess(args=[], returncode=1, stdout="", stderr=""),
        subprocess.CompletedProcess(args=[], returncode=0, stdout="", stderr=""),
    ]
    with (
        patch("subprocess.run", side_effect=responses),
        pytest.raises(RmapiError, match="Could not find"),
    ):
        client.download("/Obsidian/ghost", tmp_path)
