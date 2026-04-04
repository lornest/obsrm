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

from obsidian_remarkable_sync.config import load_config
from obsidian_remarkable_sync.converter import ConversionError, convert_file
from obsidian_remarkable_sync.remarkable import RemarkableClient, RmapiError
from obsidian_remarkable_sync.sync_state import Changeset, SyncState
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
    """Scan the vault and sync changed files to reMarkable."""
    vault_path = _resolve_vault_path(vault_path)
    config = load_config(vault_path, config_path)

    click.echo(f"Vault: {vault_path}")
    click.echo(f"Target: {config.remarkable.target_folder}")
    click.echo(f"Format: {config.remarkable.format}")

    # Scan vault
    click.echo("Scanning vault...")
    current_files = scan_vault(
        vault_path, config.vault.include, config.vault.exclude
    )
    click.echo(f"Found {len(current_files)} files")

    # Compute changeset
    state_file = vault_path / config.sync.state_file
    state = SyncState(state_file)

    if force:
        changeset = Changeset(added=list(current_files.keys()))
    else:
        changeset = state.compute_changeset(
            current_files, config.sync.delete_removed
        )

    click.echo(f"Changes: {changeset.summary()}")

    if not changeset.has_changes:
        click.echo("Nothing to sync.")
        return

    if dry_run:
        _print_changeset(changeset)
        click.echo("(dry run -- no changes made)")
        return

    # Initialize reMarkable client
    try:
        client = RemarkableClient()
    except RmapiError as e:
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    output_dir = Path(tempfile.mkdtemp(prefix="obsidian-sync-"))

    # Process additions and modifications
    to_process = changeset.added + changeset.modified
    total = len(to_process) + len(changeset.deleted)
    errors = 0
    done = 0

    try:
        for i, rel_path in enumerate(to_process, 1):
            click.echo(f"[{i}/{total}] {rel_path}")
            file_path = vault_path / rel_path
            remote_path = resolve_remote_path(
                rel_path, config.remarkable.target_folder, config.sync.flatten
            )

            try:
                output_path = convert_file(
                    file_path, vault_path, config.remarkable.format, output_dir
                )
            except ConversionError as e:
                click.echo(f"  Conversion failed: {e}", err=True)
                errors += 1
                continue

            try:
                remote_folder = remote_path.rsplit("/", 1)[0] or "/"
                if rel_path in changeset.modified:
                    client.replace(output_path, remote_path)
                else:
                    client.upload(output_path, remote_folder)
            except RmapiError as e:
                click.echo(f"  Upload failed: {e}", err=True)
                errors += 1
                continue

            # Update and save state after each successful upload
            state.update_entry(rel_path, current_files[rel_path], remote_path)
            state.save()
            done += 1
            click.echo(f"  -> {remote_path}")

            # Rate limit between rmapi calls
            if i < len(to_process):
                time.sleep(RMAPI_DELAY)

        # Process deletions
        for i, rel_path in enumerate(changeset.deleted, 1):
            remote_path = state.entries[rel_path].remote_path
            idx = len(to_process) + i
            click.echo(f"[{idx}/{total}] Deleting: {remote_path}")
            try:
                client.delete(remote_path)
            except RmapiError as e:
                click.echo(f"  Delete failed: {e}", err=True)
                errors += 1
                continue
            state.remove_entry(rel_path)
            state.save()
            done += 1

            if i < len(changeset.deleted):
                time.sleep(RMAPI_DELAY)

        # Clean up empty folders after deletions
        if changeset.deleted:
            _cleanup_empty_folders(
                changeset.deleted, config.remarkable.target_folder, client
            )

    except (KeyboardInterrupt, Exception) as e:
        click.echo(f"\n\nInterrupted: {e}" if not isinstance(e, KeyboardInterrupt) else "\n\nInterrupted by user.", err=True)
        click.echo(f"Progress saved: {done} files synced before interruption.", err=True)
        sys.exit(1)

    click.echo(f"\nState saved to {state_file}")

    if errors:
        click.echo(f"Completed: {done} succeeded, {errors} failed.", err=True)
        sys.exit(1)
    else:
        click.echo(f"Sync complete: {done} files processed.")


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

    checked: set[str] = set()
    for folder in sorted_folders:
        # Walk up the tree
        current = folder
        while current and current != target_folder and current not in checked:
            checked.add(current)
            if client.is_folder_empty(current):
                click.echo(f"  Removing empty folder: {current}")
                client.delete_folder(current)
                time.sleep(RMAPI_DELAY)
                # Move up to parent
                parent = current.rsplit("/", 1)[0]
                current = parent if parent != current else ""
            else:
                break


@cli.command()
def auth() -> None:
    """Set up reMarkable Cloud authentication via rmapi."""
    rmapi = shutil.which("rmapi")
    if rmapi is None:
        click.echo(
            "rmapi is not installed. "
            "Install from https://github.com/ddvk/rmapi/releases",
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
    current_files = scan_vault(
        vault_path, config.vault.include, config.vault.exclude
    )
    changeset = state.compute_changeset(
        current_files, config.sync.delete_removed
    )
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
