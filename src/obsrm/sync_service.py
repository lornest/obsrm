"""Sync orchestration: push, pull, and re-push workflows."""

import logging
import shutil
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from obsrm.config import Config
from obsrm.converter import ConversionError, convert_file
from obsrm.pull import pull_file, remote_path_to_vault_rel
from obsrm.remarkable import RemarkableClient, RmapiError
from obsrm.sync_state import Changeset, FileEntry, SyncState
from obsrm.vault import check_remote_path_collisions, hash_file, resolve_remote_path

logger = logging.getLogger(__name__)

# Delay between rmapi calls to avoid rate limiting (seconds)
RMAPI_DELAY = 0.5


class CollisionError(Exception):
    """Raised when multiple local files map to the same remote path."""

    def __init__(self, collisions: dict[str, list[str]]) -> None:
        self.collisions = collisions
        super().__init__("Remote path collisions detected")


@dataclass
class ProgressEvent:
    """Structured progress event emitted by the sync service.

    Kinds and their primary fields:
        summary     — summary (e.g. "3 added, 1 modified"), phase
        info        — message
        file_start  — index, total, rel_path (or remote_path for deletes)
        file_done   — rel_path, remote_path, phase
        file_error  — error, phase ("conversion", "upload", "delete", "pull", "re_push")
        changeset   — op ("+", "~", "-", "^"), rel_path, suffix
        folder_removed — remote_path
    """

    kind: str
    rel_path: str = ""
    remote_path: str = ""
    index: int = 0
    total: int = 0
    error: str = ""
    summary: str = ""
    message: str = ""
    phase: str = ""
    op: str = ""
    suffix: str = ""
    changed: bool = False


ProgressCallback = Callable[[ProgressEvent], None]


@dataclass
class PushResult:
    done: int = 0
    errors: int = 0
    pushed_remote_paths: set[str] = field(default_factory=set)
    deleted_remote_paths: set[str] = field(default_factory=set)


@dataclass
class PullResult:
    pulled: int = 0
    errors: int = 0
    deleted: int = 0
    re_push_paths: list[str] = field(default_factory=list)


@dataclass
class RePushResult:
    done: int = 0
    errors: int = 0


class SyncService:
    """Orchestrates push/pull/re-push sync workflows.

    Progress is reported via a structured ProgressEvent callback.
    The caller (e.g. CLI) decides how to render events.
    """

    def __init__(
        self,
        client: RemarkableClient,
        vault_path: Path,
        config: Config,
        state: SyncState,
        on_progress: ProgressCallback | None = None,
    ) -> None:
        self.client = client
        self.vault_path = vault_path
        self.config = config
        self.state = state
        self._on_progress = on_progress or (lambda _: None)

    def _emit(self, **kwargs: object) -> None:
        self._on_progress(ProgressEvent(**kwargs))  # type: ignore[arg-type]

    def push(
        self,
        current_files: dict[str, str],
        dry_run: bool = False,
        force: bool = False,
    ) -> PushResult:
        """Push local changes to reMarkable.

        Raises CollisionError if multiple local files map to the same remote path.
        """
        collisions = check_remote_path_collisions(
            list(current_files.keys()),
            self.config.remarkable.target_folder,
            self.config.sync.flatten,
        )
        if collisions:
            raise CollisionError(collisions)

        if force:
            changeset = Changeset(added=list(current_files.keys()))
        else:
            changeset = self.state.compute_changeset(
                current_files, self.config.sync.delete_removed
            )

        self._emit(kind="summary", phase="push", summary=changeset.summary())

        if not changeset.has_changes:
            return PushResult()

        if dry_run:
            self._emit_changeset(changeset)
            return PushResult()

        output_dir = Path(tempfile.mkdtemp(prefix="obsidian-sync-"))
        result = PushResult()

        to_process = changeset.added + changeset.modified
        total = len(to_process) + len(changeset.deleted)

        try:
            for i, rel_path in enumerate(to_process, 1):
                self._emit(
                    kind="file_start",
                    index=i,
                    total=total,
                    rel_path=rel_path,
                )
                file_path = self.vault_path / rel_path
                remote_path = resolve_remote_path(
                    rel_path,
                    self.config.remarkable.target_folder,
                    self.config.sync.flatten,
                )

                try:
                    output_path = convert_file(
                        file_path,
                        self.vault_path,
                        self.config.remarkable.format,
                        output_dir,
                    )
                except ConversionError as e:
                    self._emit(
                        kind="file_error", phase="conversion", error=str(e)
                    )
                    result.errors += 1
                    continue

                try:
                    remote_folder = remote_path.rsplit("/", 1)[0] or "/"
                    if rel_path in changeset.modified:
                        self.client.replace(output_path, remote_path)
                    else:
                        self.client.upload(output_path, remote_folder)
                except RmapiError as e:
                    self._emit(
                        kind="file_error", phase="upload", error=str(e)
                    )
                    result.errors += 1
                    continue

                self.state.update_entry(
                    rel_path, current_files[rel_path], remote_path
                )
                self.state.save()
                result.pushed_remote_paths.add(remote_path)
                result.done += 1
                self._emit(
                    kind="file_done",
                    phase="push",
                    rel_path=rel_path,
                    remote_path=remote_path,
                )

                if i < len(to_process):
                    time.sleep(RMAPI_DELAY)
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

        # Process deletions
        for i, rel_path in enumerate(changeset.deleted, 1):
            remote_path = self.state.entries[rel_path].remote_path
            idx = len(to_process) + i
            self._emit(
                kind="file_start",
                phase="delete",
                index=idx,
                total=total,
                remote_path=remote_path,
            )
            try:
                self.client.delete(remote_path)
            except RmapiError as e:
                self._emit(kind="file_error", phase="delete", error=str(e))
                result.errors += 1
                continue
            result.deleted_remote_paths.add(remote_path)
            self.state.remove_entry(rel_path)
            self.state.save()
            result.done += 1

            if i < len(changeset.deleted):
                time.sleep(RMAPI_DELAY)

        if changeset.deleted:
            self._cleanup_empty_folders(changeset.deleted)

        return result

    def pull(
        self,
        remote_files: dict[str, str],
        dry_run: bool = False,
        listing_complete: bool = True,
    ) -> PullResult:
        """Pull remote changes to Obsidian."""
        known_remotes = self.state.known_remote_paths()

        new_files = [path for path in remote_files if path not in known_remotes]
        changed_files: list[str] = []

        # Check pull-origin files for modifications via rmapi stat
        pull_origin_remotes = [
            path
            for path in remote_files
            if path in known_remotes
            and (e := self.state.entry_for_remote(path))
            and e.origin == "pull"
        ]
        if pull_origin_remotes:
            self._emit(kind="info", message="Checking for remote changes...")
            for remote_path in pull_origin_remotes:
                entry = self.state.entry_for_remote(remote_path)
                if not entry:
                    continue
                try:
                    metadata = self.client.stat(remote_path)
                    remote_mod = metadata.get("ModifiedClient", "")
                except RmapiError:
                    logger.debug(
                        "Could not stat %s, skipping change check", remote_path
                    )
                    continue
                if remote_mod and remote_mod != entry.remote_modified:
                    changed_files.append(remote_path)

        # Detect files deleted on reMarkable (only if listing was complete)
        remote_path_set = set(remote_files.keys())
        deleted_pull: list[FileEntry] = []
        re_push: list[str] = []
        if listing_complete:
            for entry in list(self.state.entries.values()):
                if entry.remote_path and entry.remote_path not in remote_path_set:
                    if entry.origin == "pull":
                        deleted_pull.append(entry)
                    else:
                        re_push.append(entry.rel_path)
                        self.state.remove_entry(entry.rel_path)

        pull_files = [(p, "new") for p in new_files] + [
            (p, "changed") for p in changed_files
        ]
        has_work = pull_files or deleted_pull or re_push

        if not has_work:
            self._emit(kind="summary", phase="pull", summary="no changes")
            return PullResult()

        if new_files:
            self._emit(
                kind="summary",
                phase="pull_new",
                summary=f"{len(new_files)} new",
            )
        if changed_files:
            self._emit(
                kind="summary",
                phase="pull_changed",
                summary=f"{len(changed_files)} changed",
            )
        if deleted_pull:
            self._emit(
                kind="summary",
                phase="pull_deleted",
                summary=f"{len(deleted_pull)} deleted on reMarkable (removing locally)",
            )
        if re_push:
            self._emit(
                kind="summary",
                phase="pull_repush",
                summary=f"{len(re_push)} deleted on reMarkable (will re-push)",
            )

        if dry_run:
            for path, file_kind in pull_files:
                rel = remote_path_to_vault_rel(
                    path, self.config.remarkable.target_folder
                )
                marker = "+" if file_kind == "new" else "~"
                self._emit(kind="changeset", op=marker, rel_path=rel)
            for entry in deleted_pull:
                self._emit(kind="changeset", op="-", rel_path=entry.rel_path)
            for rel_path in re_push:
                self._emit(
                    kind="changeset",
                    op="^",
                    rel_path=rel_path,
                    suffix="(re-push)",
                )
            return PullResult(re_push_paths=re_push)

        # Process deletions first
        for entry in deleted_pull:
            _delete_pulled_file(
                self.vault_path, entry, self.config.pull.attachments_folder
            )
            self._emit(
                kind="file_done", phase="delete_local", rel_path=entry.rel_path
            )
            self.state.remove_entry(entry.rel_path)
        if deleted_pull or re_push:
            self.state.save()

        result = PullResult(deleted=len(deleted_pull), re_push_paths=re_push)
        total = len(pull_files)

        for i, (remote_path, file_kind) in enumerate(pull_files, 1):
            rel = remote_path_to_vault_rel(
                remote_path, self.config.remarkable.target_folder
            )
            self._emit(
                kind="file_start",
                index=i,
                total=total,
                rel_path=rel,
                changed=file_kind == "changed",
            )
            try:
                md_path, att_path = pull_file(
                    self.client,
                    remote_path,
                    self.vault_path,
                    self.config.remarkable.target_folder,
                    self.config.pull.attachments_folder,
                )
            except (RmapiError, OSError) as e:
                self._emit(kind="file_error", phase="pull", error=str(e))
                result.errors += 1
                continue

            rel_local = md_path.relative_to(self.vault_path).as_posix()
            content_hash = hash_file(md_path)
            remote_modified = ""
            try:
                metadata = self.client.stat(remote_path)
                remote_modified = metadata.get("ModifiedClient", "")
            except RmapiError:
                pass
            self.state.update_entry(
                rel_local, content_hash, remote_path, remote_modified, "pull"
            )
            self.state.save()
            result.pulled += 1
            self._emit(
                kind="file_done",
                phase="pull",
                rel_path=rel_local,
            )

            if i < total:
                time.sleep(RMAPI_DELAY)

        return result

    def re_push(
        self,
        re_push_paths: list[str],
        current_files: dict[str, str],
    ) -> RePushResult:
        """Re-push files that were deleted on reMarkable but still exist locally."""
        re_push_local = [p for p in re_push_paths if p in current_files]
        if not re_push_local:
            return RePushResult()

        self._emit(
            kind="info",
            message=f"Re-pushing {len(re_push_local)} files deleted on reMarkable...",
        )
        output_dir = Path(tempfile.mkdtemp(prefix="obsidian-repush-"))
        result = RePushResult()

        try:
            for rel_path in re_push_local:
                file_path = self.vault_path / rel_path
                remote_path = resolve_remote_path(
                    rel_path,
                    self.config.remarkable.target_folder,
                    self.config.sync.flatten,
                )
                try:
                    output_path = convert_file(
                        file_path,
                        self.vault_path,
                        self.config.remarkable.format,
                        output_dir,
                    )
                    remote_folder = remote_path.rsplit("/", 1)[0] or "/"
                    self.client.upload(output_path, remote_folder)
                except (ConversionError, RmapiError) as e:
                    self._emit(
                        kind="file_error",
                        phase="re_push",
                        rel_path=rel_path,
                        error=str(e),
                    )
                    result.errors += 1
                    continue
                self.state.update_entry(
                    rel_path, current_files[rel_path], remote_path
                )
                self.state.save()
                result.done += 1
                self._emit(
                    kind="file_done",
                    phase="re_push",
                    rel_path=rel_path,
                    remote_path=remote_path,
                )
                time.sleep(RMAPI_DELAY)
        finally:
            shutil.rmtree(output_dir, ignore_errors=True)

        return result

    def _emit_changeset(self, changeset: Changeset) -> None:
        for rel_path in changeset.added:
            self._emit(kind="changeset", op="+", rel_path=rel_path)
        for rel_path in changeset.modified:
            self._emit(kind="changeset", op="~", rel_path=rel_path)
        for rel_path in changeset.deleted:
            self._emit(kind="changeset", op="-", rel_path=rel_path)

    def _cleanup_empty_folders(self, deleted_paths: list[str]) -> None:
        """Remove empty folders on reMarkable after file deletions."""

        def on_removed(remote_path: str) -> None:
            self._emit(kind="folder_removed", remote_path=remote_path)

        cleanup_empty_folders(
            deleted_paths,
            self.config.remarkable.target_folder,
            self.client,
            on_removed,
        )


def cleanup_empty_folders(
    deleted_paths: list[str],
    target_folder: str,
    client: RemarkableClient,
    on_removed: Callable[[str], None] | None = None,
) -> None:
    """Remove empty folders on reMarkable after file deletions.

    Walks from the deleted files' parent folders up toward target_folder,
    removing any that are empty. Processes deepest folders first.
    """
    folders_to_check: set[str] = set()
    for rel_path in deleted_paths:
        parts = rel_path.rsplit("/", 1)
        if len(parts) > 1:
            folder = f"{target_folder}/{parts[0]}"
            folders_to_check.add(folder)

    if not folders_to_check:
        return

    sorted_folders = sorted(
        folders_to_check, key=lambda f: f.count("/"), reverse=True
    )

    deleted_folders: set[str] = set()
    for folder in sorted_folders:
        current = folder
        while current and current != target_folder:
            if current in deleted_folders:
                parent = current.rsplit("/", 1)[0]
                current = parent if parent != current else ""
                continue
            if client.is_folder_empty(current):
                if on_removed:
                    on_removed(current)
                client.delete_folder(current)
                deleted_folders.add(current)
                time.sleep(RMAPI_DELAY)
                parent = current.rsplit("/", 1)[0]
                current = parent if parent != current else ""
            else:
                break


def _delete_pulled_file(
    vault_path: Path, entry: FileEntry, attachments_folder: str
) -> None:
    """Delete a pulled file's markdown and any associated attachments."""
    md_path = vault_path / entry.rel_path
    if md_path.exists():
        md_path.unlink()

    rel = Path(entry.rel_path)
    name = rel.stem
    rel_dir = rel.parent

    att_dir = vault_path / attachments_folder
    if rel_dir != Path("."):
        att_dir = att_dir / rel_dir

    att_root = vault_path / attachments_folder
    if att_dir.exists():
        for att_file in att_dir.iterdir():
            if att_file.stem == name or att_file.stem.startswith(f"{name}_p"):
                att_file.unlink()
        current = att_dir
        while current != att_root and current.exists() and not any(current.iterdir()):
            current.rmdir()
            current = current.parent

    md_dir = md_path.parent
    while md_dir != vault_path and md_dir.exists() and not any(md_dir.iterdir()):
        md_dir.rmdir()
        md_dir = md_dir.parent
