"""Tests for vault scanning."""

from pathlib import Path

from obsidian_remarkable_sync.vault import resolve_remote_path, scan_vault

FIXTURES = Path(__file__).parent / "fixtures" / "sample_vault"


def test_scan_vault_finds_markdown_files():
    files = scan_vault(FIXTURES, ["**/*.md"], [])
    assert "note1.md" in files
    assert "note2.md" in files
    assert "subfolder/note3.md" in files


def test_scan_vault_excludes_hidden_folders():
    """All dotfolders (.obsidian, .hidden, .git, etc.) are excluded automatically."""
    files = scan_vault(FIXTURES, ["**/*.md"], [])
    paths = set(files.keys())
    assert not any(p.startswith(".") for p in paths)
    assert ".obsidian" not in str(paths)
    assert ".hidden/secret.md" not in paths


def test_scan_vault_exclude_pattern():
    files = scan_vault(FIXTURES, ["**/*.md"], ["subfolder/**"])
    assert "subfolder/note3.md" not in files
    assert "note1.md" in files


def test_scan_vault_returns_consistent_hashes():
    files1 = scan_vault(FIXTURES, ["**/*.md"], [])
    files2 = scan_vault(FIXTURES, ["**/*.md"], [])
    assert files1 == files2


def test_resolve_remote_path_mirrored():
    assert resolve_remote_path("note.md", "/Obsidian", False) == "/Obsidian/note"
    assert (
        resolve_remote_path("subfolder/note.md", "/Obsidian", False)
        == "/Obsidian/subfolder/note"
    )
    assert (
        resolve_remote_path("a/b/note.md", "/Obsidian", False)
        == "/Obsidian/a/b/note"
    )


def test_resolve_remote_path_flattened():
    assert resolve_remote_path("note.md", "/Obsidian", True) == "/Obsidian/note"
    assert (
        resolve_remote_path("subfolder/note.md", "/Obsidian", True)
        == "/Obsidian/note"
    )
