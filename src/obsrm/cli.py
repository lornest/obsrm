"""CLI entry point for obsrm."""

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import click
from pydantic import ValidationError

from obsrm.config import Config, load_config
from obsrm.pull import list_remote_files
from obsrm.remarkable import RemarkableClient, RmapiError
from obsrm.sync_service import CollisionError, ProgressEvent, SyncService
from obsrm.sync_state import Changeset, SyncState
from obsrm.vault import scan_vault

logger = logging.getLogger(__name__)


def _render_progress(event: ProgressEvent) -> None:
    """Render a structured ProgressEvent to the terminal."""
    match event.kind:
        case "summary":
            prefix = {"push": "Push", "pull": "Pull"}.get(event.phase, "Pull")
            click.echo(f"{prefix}: {event.summary}")
        case "info":
            click.echo(event.message)
        case "file_start":
            if event.phase == "delete":
                click.echo(f"  [{event.index}/{event.total}] Deleting: {event.remote_path}")
            else:
                label = " (changed)" if event.changed else ""
                click.echo(f"  [{event.index}/{event.total}] {event.rel_path}{label}")
        case "file_done":
            if event.phase == "delete_local":
                click.echo(f"  Deleted {event.rel_path}")
            elif event.phase == "re_push":
                click.echo(f"  ^ {event.rel_path} -> {event.remote_path}")
            else:
                click.echo(f"    -> {event.remote_path or event.rel_path}")
        case "file_error":
            labels = {
                "conversion": "Conversion failed",
                "upload": "Upload failed",
                "delete": "Delete failed",
                "pull": "Pull failed",
                "re_push": "Re-push failed",
            }
            label = labels.get(event.phase, "Error")
            prefix = f"for {event.rel_path}: " if event.rel_path else ""
            click.echo(f"    {label}: {prefix}{event.error}", err=True)
        case "changeset":
            suffix = f" {event.suffix}" if event.suffix else ""
            click.echo(f"  {event.op} {event.rel_path}{suffix}")
        case "folder_removed":
            click.echo(f"  Removing empty folder: {event.remote_path}")


def _load_config_or_exit(vault_path: Path, config_path: Path | None = None) -> Config:
    """Load config with user-friendly error on invalid values."""
    try:
        return load_config(vault_path, config_path)
    except (ValidationError, ValueError) as e:
        click.echo(f"Error: invalid config: {e}", err=True)
        sys.exit(1)


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
    """Bidirectional sync: push local changes, then pull remote changes."""
    vault_path = _resolve_vault_path(vault_path)
    config = _load_config_or_exit(vault_path, config_path)

    click.echo(f"Vault: {vault_path}")
    click.echo(f"Target: {config.remarkable.target_folder}")
    click.echo(f"Format: {config.remarkable.format}")

    state_file = vault_path / config.sync.state_file
    state = SyncState(state_file)

    # Scan vault for push phase
    click.echo("Scanning vault...")
    current_files = scan_vault(vault_path, config.vault.include, config.vault.exclude)
    click.echo(f"Found {len(current_files)} local files")

    # Initialize reMarkable client (deferred so dry-run can show push changes without rmapi)
    try:
        client = RemarkableClient()
    except RmapiError as e:
        if dry_run:
            # Show push changeset without rmapi, skip pull phase
            if force:
                changeset = Changeset(added=list(current_files.keys()))
            else:
                changeset = state.compute_changeset(current_files, config.sync.delete_removed)
            click.echo(f"Push: {changeset.summary()}")
            if changeset.has_changes:
                _print_changeset(changeset)
            click.echo(f"\nNote: {e} — pull phase skipped.")
            click.echo("(dry run -- no changes made)")
            return
        click.echo(f"Error: {e}", err=True)
        sys.exit(1)

    svc = SyncService(client, vault_path, config, state, on_progress=_render_progress)

    # List remote files for pull phase
    click.echo("Listing remote files...")
    remote_files, listing_complete = list_remote_files(client, config.remarkable.target_folder)
    click.echo(f"Found {len(remote_files)} remote files")
    if not listing_complete:
        click.echo("WARNING: Some folders could not be listed. Skipping deletion detection.")

    try:
        # Phase 1: Push
        push = svc.push(current_files, dry_run, force)

        # Update remote listing to reflect push results
        for rp in push.pushed_remote_paths:
            remote_files[rp] = "f"
        for rp in push.deleted_remote_paths:
            remote_files.pop(rp, None)

        # Phase 2: Pull
        pull = svc.pull(remote_files, dry_run, listing_complete)

        # Phase 3: Re-push files deleted on reMarkable but still in vault
        re = None
        if pull.re_push_paths and not dry_run:
            re = svc.re_push(pull.re_push_paths, current_files)

    except CollisionError as e:
        click.echo("Error: multiple local files map to the same remote path:", err=True)
        for remote, locals in e.collisions.items():
            click.echo(f"  {remote} <- {', '.join(locals)}", err=True)
        click.echo(
            "Rename files or disable flatten to resolve collisions.", err=True
        )
        raise click.Abort() from e
    except KeyboardInterrupt:
        click.echo("\n\nInterrupted by user.", err=True)
        click.echo("Progress saved.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\n\nInterrupted: {e}", err=True)
        click.echo("Progress saved.", err=True)
        sys.exit(1)

    if dry_run:
        click.echo("(dry run -- no changes made)")
        return

    re_push_done = re.done if re else 0
    re_push_errors = re.errors if re else 0
    total_errors = push.errors + pull.errors + re_push_errors

    # Summary
    parts = []
    if push.done:
        parts.append(f"{push.done} pushed")
    if pull.pulled:
        parts.append(f"{pull.pulled} pulled")
    if pull.deleted:
        parts.append(f"{pull.deleted} deleted")
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
    vault_path = _resolve_vault_path(vault_path)
    config = _load_config_or_exit(vault_path, config_path)

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

    svc = SyncService(client, vault_path, config, state, on_progress=_render_progress)

    try:
        result = svc.pull(remote_files, dry_run, listing_complete)
    except KeyboardInterrupt:
        click.echo("\n\nInterrupted by user.", err=True)
        sys.exit(1)
    except Exception as e:
        click.echo(f"\n\nInterrupted: {e}", err=True)
        sys.exit(1)

    if dry_run:
        click.echo("(dry run -- no changes made)")
        return

    parts = []
    if result.pulled:
        parts.append(f"{result.pulled} pulled")
    if result.deleted:
        parts.append(f"{result.deleted} deleted")
    if result.re_push_paths:
        parts.append(f"{len(result.re_push_paths)} to re-push (run sync)")
    if result.errors:
        parts.append(f"{result.errors} failed")

    if parts:
        click.echo(f"\nPull complete: {', '.join(parts)}.")
    else:
        click.echo("\nNothing to pull.")

    if result.errors:
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
    config = _load_config_or_exit(vault_path, config_path)

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
