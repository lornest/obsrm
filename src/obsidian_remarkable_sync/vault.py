"""Vault scanning and file discovery."""

import hashlib
from pathlib import Path


def scan_vault(
    vault_path: Path,
    include_patterns: list[str],
    exclude_patterns: list[str],
) -> dict[str, str]:
    """Scan the vault and return a mapping of relative paths to content hashes.

    Args:
        vault_path: root directory of the Obsidian vault
        include_patterns: glob patterns for files to include
        exclude_patterns: glob patterns for files to exclude

    Returns:
        dict mapping relative file paths (posix-style) to SHA-256 content hashes
    """
    included: set[Path] = set()
    for pattern in include_patterns:
        included.update(vault_path.glob(pattern))

    excluded: set[Path] = set()
    for pattern in exclude_patterns:
        for path in vault_path.glob(pattern):
            if path.is_dir():
                # Also exclude everything inside the directory
                excluded.update(path.rglob("*"))
            excluded.add(path)

    files = sorted(included - excluded)

    result: dict[str, str] = {}
    for file_path in files:
        if not file_path.is_file():
            continue
        rel_path = file_path.relative_to(vault_path).as_posix()
        # Skip files inside hidden folders (dotfolders like .obsidian, .git, etc.)
        if any(part.startswith(".") for part in Path(rel_path).parts):
            continue
        content_hash = _hash_file(file_path)
        result[rel_path] = content_hash

    return result


def resolve_remote_path(
    rel_path: str, target_folder: str, flatten: bool
) -> str:
    """Compute the remote path on reMarkable for a given vault file.

    Args:
        rel_path: relative path within the vault (posix-style)
        target_folder: root folder on reMarkable (e.g. "/Obsidian")
        flatten: if True, place all files directly in target_folder

    Returns:
        Remote path string (e.g. "/Obsidian/subfolder/note")
    """
    p = Path(rel_path)
    # Strip .md extension for the document name on reMarkable
    name = p.stem

    if flatten:
        return f"{target_folder}/{name}"
    else:
        # Mirror folder structure
        if p.parent == Path("."):
            return f"{target_folder}/{name}"
        return f"{target_folder}/{p.parent.as_posix()}/{name}"


def _hash_file(file_path: Path) -> str:
    h = hashlib.sha256()
    with open(file_path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
