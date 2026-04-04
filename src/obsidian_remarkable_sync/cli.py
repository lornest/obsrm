"""CLI entry point for obsidian-remarkable-sync."""

import logging
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import click

from obsidian_remarkable_sync.config import Config, load_config
from obsidian_remarkable_sync.converter import ConversionError, convert_file
from obsidian_remarkable_sync.remarkable import RemarkableClient, RmapiError
from obsidian_remarkable_sync.sync_state import Changeset, FileEntry, SyncState
from obsidian_remarkable_sync.vault import resolve_remote_path, scan_vault

logger = logging.getLogger(__name__)

# Delay between rmapi calls to avoid rate limiting (seconds)
RMAPI_DELAY = 0.5


@click.group()
@click.option("--verbose", "-v", is_flag=True, help="Enable verbose output.")
def cli(verbose: bool) -> None:
    """Sync an Obsidian vault to a reMarkable tablet."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _run_push(
    client: RemarkableClient,
    vault_path: Path,
    config: Config,
    state: SyncState,
    current_files: dict[str, str],
    dry_run: bool,
    force: bool = False,
) -> tuple[int, int, set[str], set[str]]:
    """Push local changes to reMarkable.

    Returns (done, errors, pushed_remote_paths, deleted_remote_paths).
    """
    if force:
        changeset = Changeset(added=list(current_files.keys()))
    else:
        changeset = state.compute_changeset(current_files, config.sync.delete_removed)

    click.echo(f"Push: {changeset.summary()}")

    if not changeset.has_changes:
        return 0, 0, set(), set()

    if dry_run:
        _print_changeset(changeset)
        return 0, 0, set(), set()

    output_dir = Path(tempfile.mkdtemp(prefix="obsidian-sync-"))

    to_process = changeset.added + changeset.modified
    total = len(to_process) + len(changeset.deleted)
    errors = 0
    done = 0
    pushed_remotes: set[str] = set()

    for i, rel_path in enumerate(to_process, 1):
        click.echo(f"  [{i}/{total}] {rel_path}")
        file_path = vault_path / rel_path
        remote_path = resolve_remote_path(
            rel_path, config.remarkable.target_folder, config.sync.flatten
        )

        try:
            output_path = convert_file(
                file_path, vault_path, config.remarkable.format, output_dir
            )
        except ConversionError as e:
            click.echo(f"    Conversion failed: {e}", err=True)
            errors += 1
            continue

        try:
            remote_folder = remote_path.rsplit("/", 1)[0] or "/"
            if rel_path in changeset.modified:
                client.replace(output_path, remote_path)
            else:
                client.upload(output_path, remote_folder)
        except RmapiError as e:
            click.echo(f"    Upload failed: {e}", err=True)
            errors += 1
            continue

        state.update_entry(rel_path, current_files[rel_path], remote_path)
        state.save()
        pushed_remotes.add(remote_path)
        done += 1
        click.echo(f"    -> {remote_path}")

        if i < len(to_process):
            time.sleep(RMAPI_DELAY)

    # Process deletions
    deleted_remotes: set[str] = set()
    for i, rel_path in enumerate(changeset.deleted, 1):
        remote_path = state.entries[rel_path].remote_path
        idx = len(to_process) + i
        click.echo(f"  [{idx}/{total}] Deleting: {remote_path}")
        try:
            client.delete(remote_path)
        except RmapiError as e:
            click.echo(f"    Delete failed: {e}", err=True)
            errors += 1
            continue
        deleted_remotes.add(remote_path)
        state.remove_entry(rel_path)
        state.save()
        done += 1

        if i < len(changeset.deleted):
            time.sleep(RMAPI_DELAY)

    if changeset.deleted:
        _cleanup_empty_folders(changeset.deleted, config.remarkable.target_folder, client)

    return done, errors, pushed_remotes, deleted_remotes


def _run_pull(
    client: RemarkableClient,
    vault_path: Path,
    config: Config,
    state: SyncState,
    remote_files: dict[str, str],
    dry_run: bool,
    listing_complete: bool = True,
) -> tuple[int, int, int, list[str]]:
    """Pull remote changes to Obsidian.

    Returns (pulled, errors, deleted, re_push_rel_paths).
    re_push_rel_paths: push-origin files deleted on reMarkable that still exist locally.
    """
    from obsidian_remarkable_sync.pull import pull_file, remote_path_to_vault_rel
    from obsidian_remarkable_sync.vault import _hash_file

    known_remotes = state.known_remote_paths()

    new_files = [path for path in remote_files if path not in known_remotes]
    changed_files: list[str] = []

    # Check pull-origin files for modifications via rmapi stat
    pull_origin_remotes = [
        path
        for path in remote_files
        if path in known_remotes and (e := state.entry_for_remote(path)) and e.origin == "pull"
    ]
    if pull_origin_remotes:
        click.echo("Checking for remote changes...")
        for remote_path in pull_origin_remotes:
            entry = state.entry_for_remote(remote_path)
            if not entry:
                continue
            try:
                metadata = client.stat(remote_path)
                remote_mod = metadata.get("ModifiedClient", "")
            except RmapiError:
                logger.debug("Could not stat %s, skipping change check", remote_path)
                continue
            if remote_mod and remote_mod != entry.remote_modified:
                changed_files.append(remote_path)

    # Detect files deleted on reMarkable (only if listing was complete)
    remote_path_set = set(remote_files.keys())
    deleted_pull: list[FileEntry] = []  # pull-origin: delete local files
    re_push: list[str] = []  # push-origin deleted on reMarkable: need re-push
    if listing_complete:
        for entry in list(state.entries.values()):
            if entry.remote_path and entry.remote_path not in remote_path_set:
                if entry.origin == "pull":
                    deleted_pull.append(entry)
                else:
                    # Push-origin deleted on reMarkable — needs re-push
                    re_push.append(entry.rel_path)
                    state.remove_entry(entry.rel_path)

    pull_files = [(p, "new") for p in new_files] + [(p, "changed") for p in changed_files]
    has_work = pull_files or deleted_pull or re_push

    if not has_work:
        click.echo("Pull: no changes")
        return 0, 0, 0, []

    if new_files:
        click.echo(f"Pull: {len(new_files)} new")
    if changed_files:
        click.echo(f"Pull: {len(changed_files)} changed")
    if deleted_pull:
        click.echo(f"Pull: {len(deleted_pull)} deleted on reMarkable (removing locally)")
    if re_push:
        click.echo(f"Pull: {len(re_push)} deleted on reMarkable (will re-push)")

    if dry_run:
        for path, kind in pull_files:
            rel = remote_path_to_vault_rel(path, config.remarkable.target_folder)
            marker = "+" if kind == "new" else "~"
            click.echo(f"  {marker} {rel}")
        for entry in deleted_pull:
            click.echo(f"  - {entry.rel_path}")
        for rel_path in re_push:
            click.echo(f"  ^ {rel_path} (re-push)")
        return 0, 0, 0, re_push

    # Process deletions first
    for entry in deleted_pull:
        _delete_pulled_file(vault_path, entry, config.pull.attachments_folder)
        click.echo(f"  Deleted {entry.rel_path}")
        state.remove_entry(entry.rel_path)
    if deleted_pull or re_push:
        state.save()

    errors = 0
    done = 0
    total = len(pull_files)

    for i, (remote_path, kind) in enumerate(pull_files, 1):
        rel = remote_path_to_vault_rel(remote_path, config.remarkable.target_folder)
        label = "(changed)" if kind == "changed" else ""
        click.echo(f"  [{i}/{total}] {rel} {label}".rstrip())
        try:
            md_path, att_path = pull_file(
                client,
                remote_path,
                vault_path,
                config.remarkable.target_folder,
                config.pull.attachments_folder,
            )
        except (RmapiError, OSError) as e:
            click.echo(f"    Pull failed: {e}", err=True)
            errors += 1
            continue

        rel_local = md_path.relative_to(vault_path).as_posix()
        content_hash = _hash_file(md_path)
        remote_modified = ""
        try:
            metadata = client.stat(remote_path)
            remote_modified = metadata.get("ModifiedClient", "")
        except RmapiError:
            pass
        state.update_entry(rel_local, content_hash, remote_path, remote_modified, "pull")
        state.save()
        done += 1
        click.echo(f"    -> {rel_local}")

        if i < total:
            time.sleep(RMAPI_DELAY)

    return done, errors, len(deleted_pull), re_push


@cli.command()
@click.option(
    "--vault-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Path to the Obsidian vault. Defaults to current directory.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to sync-config.yaml.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be synced without doing it.")
@click.option("--force", is_flag=True, help="Re-sync all files regardless of state.")
def sync(
    vault_path: Path | None,
    config_path: Path | None,
    dry_run: bool,
    force: bool,
) -> None:
    """Bidirectional sync: push local changes, then pull remote changes."""
    from obsidian_remarkable_sync.pull import list_remote_files

    vault_path = _resolve_vault_path(vault_path)
    config = load_config(vault_path, config_path)

    click.echo(f"Vault: {vault_path}")
    click.echo(f"Target: {config.remarkable.target_folder}")
    click.echo(f"Format: {config.remarkable.format}")

    # Initialize reMarkable client
    try:
        client = RemarkableClient()
    except RmapiError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    state_file = vault_path / config.sync.state_file
    state = SyncState(state_file)

    # Scan vault for push phase
    click.echo("Scanning vault...")
    current_files = scan_vault(vault_path, config.vault.include, config.vault.exclude)
    click.echo(f"Found {len(current_files)} local files")

    # List remote files for pull phase
    click.echo("Listing remote files...")
    remote_files, listing_complete = list_remote_files(client, config.remarkable.target_folder)
    click.echo(f"Found {len(remote_files)} remote files")
    if not listing_complete:
        click.echo("WARNING: Some folders could not be listed. Skipping deletion detection.")

    try:
        # Phase 1: Push
        push_done, push_errors, pushed_remotes, deleted_remotes = _run_push(
            client, vault_path, config, state, current_files, dry_run, force
        )

        # Update remote listing to reflect push results:
        # - Add just-pushed paths so pull doesn't treat them as deleted
        # - Remove just-deleted paths so pull doesn't try to re-pull them
        for rp in pushed_remotes:
            remote_files[rp] = "f"
        for rp in deleted_remotes:
            remote_files.pop(rp, None)

        # Phase 2: Pull
        pull_done, pull_errors, pull_deleted, re_push_paths = _run_pull(
            client, vault_path, config, state, remote_files, dry_run, listing_complete
        )

        # Phase 3: Re-push files deleted on reMarkable but still in vault
        re_push_done = 0
        re_push_errors = 0
        if re_push_paths:
            # Filter to files that still exist locally
            re_push_local = [p for p in re_push_paths if p in current_files]
            if re_push_local and not dry_run:
                click.echo(f"Re-pushing {len(re_push_local)} files deleted on reMarkable...")
                output_dir = Path(tempfile.mkdtemp(prefix="obsidian-repush-"))
                for rel_path in re_push_local:
                    file_path = vault_path / rel_path
                    remote_path = resolve_remote_path(
                        rel_path, config.remarkable.target_folder, config.sync.flatten
                    )
                    try:
                        output_path = convert_file(
                            file_path, vault_path, config.remarkable.format, output_dir
                        )
                        remote_folder = remote_path.rsplit("/", 1)[0] or "/"
                        client.upload(output_path, remote_folder)
                    except (ConversionError, RmapiError) as e:
                        click.echo(f"  Re-push failed for {rel_path}: {e}", err=True)
                        re_push_errors += 1
                        continue
                    state.update_entry(rel_path, current_files[rel_path], remote_path)
                    state.save()
                    re_push_done += 1
                    click.echo(f"  ^ {rel_path} -> {remote_path}")
                    time.sleep(RMAPI_DELAY)

    except (KeyboardInterrupt, Exception) as e:
        if isinstance(e, KeyboardInterrupt):
            click.echo("\n\nInterrupted by user.", err=True)
        else:
            click.echo(f"\n\nInterrupted: {e}", err=True)
        click.echo("Progress saved.", err=True)
        sys.exit(1)

    if dry_run:
        click.echo("(dry run -- no changes made)")
        return

    total_errors = push_errors + pull_errors + re_push_errors

    # Summary
    parts = []
    if push_done:
        parts.append(f"{push_done} pushed")
    if pull_done:
        parts.append(f"{pull_done} pulled")
    if pull_deleted:
        parts.append(f"{pull_deleted} deleted")
    if re_push_done:
        parts.append(f"{re_push_done} re-pushed")
    if total_errors:
        parts.append(f"{total_errors} failed")

    if parts:
        click.echo(f"\nSync complete: {', '.join(parts)}.")
    else:
        click.echo("\nNothing to sync.")

    if total_errors:
        sys.exit(1)


def _cleanup_empty_folders(
    deleted_paths: list[str],
    target_folder: str,
    client: RemarkableClient,
) -> None:
    """Remove empty folders on reMarkable after file deletions.

    Walks from the deleted files' parent folders up toward target_folder,
    removing any that are empty. Processes deepest folders first.
    """
    # Collect all parent folders from deleted files' remote paths
    folders_to_check: set[str] = set()
    for rel_path in deleted_paths:
        # Build the remote path from the rel_path
        parts = rel_path.rsplit("/", 1)
        if len(parts) > 1:
            # File was in a subfolder
            folder = f"{target_folder}/{parts[0]}"
            folders_to_check.add(folder)

    if not folders_to_check:
        return

    # Sort deepest first so children are removed before parents
    sorted_folders = sorted(folders_to_check, key=lambda f: f.count("/"), reverse=True)

    deleted_folders: set[str] = set()
    for folder in sorted_folders:
        # Walk up the tree
        current = folder
        while current and current != target_folder:
            if current in deleted_folders:
                # Already removed this folder, move up
                parent = current.rsplit("/", 1)[0]
                current = parent if parent != current else ""
                continue
            if client.is_folder_empty(current):
                click.echo(f"  Removing empty folder: {current}")
                client.delete_folder(current)
                deleted_folders.add(current)
                time.sleep(RMAPI_DELAY)
                # Move up to parent
                parent = current.rsplit("/", 1)[0]
                current = parent if parent != current else ""
            else:
                break


def _delete_pulled_file(vault_path: Path, entry: FileEntry, attachments_folder: str) -> None:
    """Delete a pulled file's markdown and any associated attachments."""
    md_path = vault_path / entry.rel_path
    if md_path.exists():
        md_path.unlink()

    # Derive attachment location: attachments_folder / parent_dir / name.*
    rel = Path(entry.rel_path)
    name = rel.stem
    rel_dir = rel.parent

    att_dir = vault_path / attachments_folder
    if rel_dir != Path("."):
        att_dir = att_dir / rel_dir

    att_root = vault_path / attachments_folder
    if att_dir.exists():
        # Remove matching attachments (pdf, rmdoc, svg pages)
        for att_file in att_dir.iterdir():
            if att_file.stem == name or att_file.stem.startswith(f"{name}_p"):
                att_file.unlink()
        # Clean up empty directories up to attachments root
        current = att_dir
        while current != att_root and current.exists() and not any(current.iterdir()):
            current.rmdir()
            current = current.parent

    # Clean up empty markdown parent directories
    md_dir = md_path.parent
    while md_dir != vault_path and md_dir.exists() and not any(md_dir.iterdir()):
        md_dir.rmdir()
        md_dir = md_dir.parent


@cli.command()
@click.option(
    "--vault-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
    help="Path to the Obsidian vault. Defaults to current directory.",
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
    help="Path to sync-config.yaml.",
)
@click.option("--dry-run", is_flag=True, help="Show what would be pulled without doing it.")
def pull(
    vault_path: Path | None,
    config_path: Path | None,
    dry_run: bool,
) -> None:
    """Pull new/changed files from reMarkable to the vault."""
    from obsidian_remarkable_sync.pull import list_remote_files

    vault_path = _resolve_vault_path(vault_path)
    config = load_config(vault_path, config_path)

    click.echo(f"Vault: {vault_path}")
    click.echo(f"Source: {config.remarkable.target_folder}")
    click.echo(f"Attachments: {config.pull.attachments_folder}")

    try:
        client = RemarkableClient()
    except RmapiError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    click.echo("Listing remote files...")
    remote_files, listing_complete = list_remote_files(client, config.remarkable.target_folder)
    click.echo(f"Found {len(remote_files)} files on reMarkable")
    if not listing_complete:
        click.echo("WARNING: Some folders could not be listed. Skipping deletion detection.")

    state_file = vault_path / config.sync.state_file
    state = SyncState(state_file)

    try:
        pulled, errors, deleted, re_push = _run_pull(
            client, vault_path, config, state, remote_files, dry_run, listing_complete
        )
    except (KeyboardInterrupt, Exception) as e:
        if isinstance(e, KeyboardInterrupt):
            click.echo("\n\nInterrupted by user.", err=True)
        else:
            click.echo(f"\n\nInterrupted: {e}", err=True)
        sys.exit(1)

    if dry_run:
        click.echo("(dry run -- no changes made)")
        return

    parts = []
    if pulled:
        parts.append(f"{pulled} pulled")
    if deleted:
        parts.append(f"{deleted} deleted")
    if re_push:
        parts.append(f"{len(re_push)} to re-push (run sync)")
    if errors:
        parts.append(f"{errors} failed")

    if parts:
        click.echo(f"\nPull complete: {', '.join(parts)}.")
    else:
        click.echo("\nNothing to pull.")

    if errors:
        sys.exit(1)


@cli.command()
def auth() -> None:
    """Set up reMarkable Cloud authentication via rmapi."""
    rmapi = shutil.which("rmapi")
    if rmapi is None:
        click.echo(
            "rmapi is not installed. Install from https://github.com/ddvk/rmapi/releases",
            err=True,
        )
        sys.exit(1)

    # Check if already authenticated
    rmapi_conf = Path.home() / ".rmapi" / "rmapi.conf"
    if rmapi_conf.exists() and rmapi_conf.stat().st_size > 0:
        click.echo(f"Existing rmapi config found at {rmapi_conf}")
        if not click.confirm("Re-authenticate?", default=False):
            click.echo("Keeping existing authentication.")
            return

    click.echo("Starting rmapi authentication...")
    click.echo(
        "You will be prompted to enter a one-time code from\n"
        "https://my.remarkable.com/device/browser/connect"
    )
    subprocess.run([rmapi], check=False)


@cli.command()
@click.option(
    "--vault-path",
    type=click.Path(exists=True, file_okay=False, path_type=Path),
    default=None,
)
@click.option(
    "--config",
    "config_path",
    type=click.Path(exists=True, dir_okay=False, path_type=Path),
    default=None,
)
def status(vault_path: Path | None, config_path: Path | None) -> None:
    """Show the current sync state."""
    vault_path = _resolve_vault_path(vault_path)
    config = load_config(vault_path, config_path)

    state_file = vault_path / config.sync.state_file
    state = SyncState(state_file)

    click.echo(f"Vault:  {vault_path}")
    click.echo(f"Target: {config.remarkable.target_folder}")
    click.echo(f"Format: {config.remarkable.format}")
    click.echo(f"State:  {state_file}")
    click.echo()

    if not state.entries:
        click.echo("No files have been synced yet.")
        return

    click.echo(f"Synced files ({len(state.entries)}):")
    for entry in sorted(state.entries.values(), key=lambda e: e.rel_path):
        click.echo(f"  {entry.rel_path} -> {entry.remote_path}")

    # Show pending changes
    current_files = scan_vault(vault_path, config.vault.include, config.vault.exclude)
    changeset = state.compute_changeset(current_files, config.sync.delete_removed)
    if changeset.has_changes:
        click.echo(f"\nPending changes: {changeset.summary()}")
        _print_changeset(changeset)
    else:
        click.echo("\nEverything is up to date.")


def _resolve_vault_path(vault_path: Path | None) -> Path:
    if vault_path is not None:
        return vault_path.resolve()
    env_path = os.environ.get("VAULT_PATH")
    if env_path:
        return Path(env_path).resolve()
    return Path.cwd()


def _print_changeset(changeset: Changeset) -> None:
    for rel_path in changeset.added:
        click.echo(f"  + {rel_path}")
    for rel_path in changeset.modified:
        click.echo(f"  ~ {rel_path}")
    for rel_path in changeset.deleted:
        click.echo(f"  - {rel_path}")
