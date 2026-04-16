"""Sync state persistence and changeset computation."""

import json
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileEntry:
    rel_path: str
    content_hash: str
    remote_path: str
    remote_modified: str = ""
    origin: str = "push"


@dataclass
class Changeset:
    added: list[str] = field(default_factory=list)
    modified: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return bool(self.added or self.modified or self.deleted)

    def summary(self) -> str:
        parts = []
        if self.added:
            parts.append(f"{len(self.added)} added")
        if self.modified:
            parts.append(f"{len(self.modified)} modified")
        if self.deleted:
            parts.append(f"{len(self.deleted)} deleted")
        return ", ".join(parts) if parts else "no changes"


class SyncState:
    """Manages the .sync-state.json file tracking what has been synced."""

    def __init__(self, state_file: Path) -> None:
        self.state_file = state_file
        self.entries: dict[str, FileEntry] = {}
        if state_file.exists() and state_file.stat().st_size > 0:
            self._load()

    def _load(self) -> None:
        with open(self.state_file) as f:
            data = json.load(f)
        for rel_path, entry_data in data.get("files", {}).items():
            self.entries[rel_path] = FileEntry(
                rel_path=rel_path,
                content_hash=entry_data["content_hash"],
                remote_path=entry_data["remote_path"],
                remote_modified=entry_data.get("remote_modified", ""),
                origin=entry_data.get("origin", "push"),
            )

    def save(self) -> None:
        data = {
            "files": {
                entry.rel_path: {
                    "content_hash": entry.content_hash,
                    "remote_path": entry.remote_path,
                    "remote_modified": entry.remote_modified,
                    "origin": entry.origin,
                }
                for entry in self.entries.values()
            }
        }
        # Write to a temp file in the same directory, then atomically replace.
        # This prevents corruption if the process is interrupted mid-write.
        parent = self.state_file.parent
        fd, tmp_path = tempfile.mkstemp(dir=parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2, sort_keys=True)
            os.replace(tmp_path, self.state_file)
        except BaseException:
            os.unlink(tmp_path)
            raise

    def compute_changeset(
        self, current_files: dict[str, str], delete_removed: bool = False
    ) -> Changeset:
        """Compare current vault files against saved state.

        Args:
            current_files: mapping of rel_path -> content_hash
            delete_removed: whether to include deleted files in changeset

        Returns:
            Changeset with added, modified, and deleted file paths.
        """
        changeset = Changeset()

        for rel_path, content_hash in current_files.items():
            if rel_path not in self.entries:
                changeset.added.append(rel_path)
            elif self.entries[rel_path].content_hash != content_hash:
                changeset.modified.append(rel_path)

        if delete_removed:
            for rel_path in self.entries:
                if rel_path not in current_files:
                    changeset.deleted.append(rel_path)

        return changeset

    def update_entry(
        self,
        rel_path: str,
        content_hash: str,
        remote_path: str,
        remote_modified: str = "",
        origin: str = "push",
    ) -> None:
        self.entries[rel_path] = FileEntry(
            rel_path=rel_path,
            content_hash=content_hash,
            remote_path=remote_path,
            remote_modified=remote_modified,
            origin=origin,
        )

    def remove_entry(self, rel_path: str) -> None:
        self.entries.pop(rel_path, None)

    def known_remote_paths(self) -> set[str]:
        """Return the set of all remote paths tracked in state."""
        return {entry.remote_path for entry in self.entries.values()}

    def entry_for_remote(self, remote_path: str) -> FileEntry | None:
        """Find the entry tracking a given remote path."""
        for entry in self.entries.values():
            if entry.remote_path == remote_path:
                return entry
        return None
