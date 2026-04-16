"""Tests for empty folder cleanup after deletions."""

from unittest.mock import MagicMock

from obsrm.sync_service import cleanup_empty_folders as _cleanup_empty_folders


def _make_mock_client(folder_contents: dict[str, list[str]]):
    """Create a mock RemarkableClient that tracks folder state.

    Args:
        folder_contents: mapping of folder path -> list of entry names.
                         Entries are removed when delete_folder is called.
    """
    client = MagicMock()
    deleted_folders: list[str] = []

    def is_folder_empty(path):
        return len(folder_contents.get(path, [])) == 0

    def delete_folder(path):
        deleted_folders.append(path)
        # Remove from parent's contents
        parent = path.rsplit("/", 1)[0]
        name = path.rsplit("/", 1)[1]
        if parent in folder_contents:
            folder_contents[parent] = [e for e in folder_contents[parent] if e != name]
        folder_contents.pop(path, None)

    client.is_folder_empty = MagicMock(side_effect=is_folder_empty)
    client.delete_folder = MagicMock(side_effect=delete_folder)
    client.deleted_folders = deleted_folders
    return client


def test_cleanup_single_empty_folder():
    """Deleting all files in a folder should remove the folder."""
    client = _make_mock_client(
        {
            "/Obsidian/Tools": [],  # empty after file deletions
            "/Obsidian": ["Tools"],
        }
    )
    _cleanup_empty_folders(
        ["Tools/Cilium.md", "Tools/Helm.md"],
        "/Obsidian",
        client,
    )
    client.delete_folder.assert_any_call("/Obsidian/Tools")


def test_cleanup_nested_empty_folders():
    """Deleting files in nested folders should clean up the entire tree."""
    # After deleting all files: AA, bp, Subsea 7 are empty,
    # so Customers should also become empty and get deleted.
    client = _make_mock_client(
        {
            "/Obsidian/GL/Customers/AA": [],
            "/Obsidian/GL/Customers/bp": [],
            "/Obsidian/GL/Customers/Subsea 7": [],
            "/Obsidian/GL/Customers": ["AA", "bp", "Subsea 7"],
            "/Obsidian/GL": ["Customers", "other-file"],
            "/Obsidian": ["GL"],
        }
    )
    _cleanup_empty_folders(
        [
            "GL/Customers/AA/file1.md",
            "GL/Customers/bp/file2.md",
            "GL/Customers/Subsea 7/file3.md",
        ],
        "/Obsidian",
        client,
    )
    assert "/Obsidian/GL/Customers/AA" in client.deleted_folders
    assert "/Obsidian/GL/Customers/bp" in client.deleted_folders
    assert "/Obsidian/GL/Customers/Subsea 7" in client.deleted_folders
    assert "/Obsidian/GL/Customers" in client.deleted_folders
    # GL still has "other-file" so should NOT be deleted
    assert "/Obsidian/GL" not in client.deleted_folders


def test_cleanup_does_not_delete_target_folder():
    """The target folder itself (/Obsidian) should never be deleted."""
    client = _make_mock_client(
        {
            "/Obsidian/Notes": [],
            "/Obsidian": ["Notes"],
        }
    )
    _cleanup_empty_folders(
        ["Notes/file.md"],
        "/Obsidian",
        client,
    )
    client.delete_folder.assert_any_call("/Obsidian/Notes")
    assert "/Obsidian" not in client.deleted_folders


def test_cleanup_non_empty_folder_kept():
    """Folders that still have files should not be deleted."""
    client = _make_mock_client(
        {
            "/Obsidian/Notes": ["remaining-file"],
        }
    )
    _cleanup_empty_folders(
        ["Notes/deleted.md"],
        "/Obsidian",
        client,
    )
    client.delete_folder.assert_not_called()


def test_cleanup_deeply_nested():
    """Walk up through multiple levels of empty folders."""
    client = _make_mock_client(
        {
            "/Obsidian/A/B/C": [],
            "/Obsidian/A/B": ["C"],
            "/Obsidian/A": ["B"],
            "/Obsidian": ["A"],
        }
    )
    _cleanup_empty_folders(
        ["A/B/C/file.md"],
        "/Obsidian",
        client,
    )
    assert "/Obsidian/A/B/C" in client.deleted_folders
    assert "/Obsidian/A/B" in client.deleted_folders
    assert "/Obsidian/A" in client.deleted_folders
    assert "/Obsidian" not in client.deleted_folders


def test_cleanup_no_subfolders():
    """Files at the root of target folder have no parent to clean up."""
    client = _make_mock_client(
        {
            "/Obsidian": [],
        }
    )
    _cleanup_empty_folders(
        ["file.md"],
        "/Obsidian",
        client,
    )
    client.delete_folder.assert_not_called()
