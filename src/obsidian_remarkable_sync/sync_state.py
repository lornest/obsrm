"""Sync state persistence and changeset computation."""

import json
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class FileEntry:
    rel_path: str
    content_hash: str
    remote_path: str


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
            )

    def save(self) -> None:
        data = {
            "files": {
                entry.rel_path: {
                    "content_hash": entry.content_hash,
                    "remote_path": entry.remote_path,
                }
                for entry in self.entries.values()
            }
        }
        with open(self.state_file, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)

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

    def update_entry(self, rel_path: str, content_hash: str, remote_path: str) -> None:
        self.entries[rel_path] = FileEntry(
            rel_path=rel_path,
            content_hash=content_hash,
            remote_path=remote_path,
        )

    def remove_entry(self, rel_path: str) -> None:
        self.entries.pop(rel_path, None)
