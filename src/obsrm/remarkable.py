"""reMarkable Cloud interface via rmapi CLI."""

import contextlib
import logging
import re
import shutil
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


class RmapiError(Exception):
    pass


class RemarkableClient:
    """Wrapper around the rmapi CLI tool for reMarkable Cloud operations."""

    def __init__(self) -> None:
        self._rmapi = shutil.which("rmapi")
        if self._rmapi is None:
            raise RmapiError(
                "rmapi is not installed. Install from https://github.com/ddvk/rmapi/releases"
            )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = [self._rmapi, *args]
        logger.debug("Running: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RmapiError(
                f"rmapi {' '.join(args)} failed (exit {result.returncode}):\n{result.stderr}"
            )
        return result

    def ensure_folder(self, remote_path: str) -> None:
        """Create folder hierarchy on reMarkable, creating parents as needed."""
        parts = [p for p in remote_path.strip("/").split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            with contextlib.suppress(RmapiError):
                self._run("mkdir", current)

    def upload(self, local_path: Path, remote_folder: str) -> None:
        """Upload a file to a folder on reMarkable, overwriting if it exists.

        Args:
            local_path: path to the local file (epub/pdf)
            remote_folder: remote folder path (e.g. "/Obsidian/subfolder")
        """
        self.ensure_folder(remote_folder)
        self._run("put", "--force", str(local_path), remote_folder)
        logger.info("Uploaded %s -> %s", local_path.name, remote_folder)

    def delete(self, remote_path: str) -> None:
        """Delete a document from reMarkable.

        Raises RmapiError if the deletion fails so callers can keep
        local state in sync with the remote.
        """
        self._run("rm", remote_path)
        logger.info("Deleted %s", remote_path)

    def list_folder(self, remote_path: str = "/") -> list[str]:
        """List contents of a folder on reMarkable."""
        result = self._run("ls", remote_path)
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]

    def is_folder_empty(self, remote_path: str) -> bool:
        """Check if a folder on reMarkable is empty."""
        try:
            entries = self.list_folder(remote_path)
            return len(entries) == 0
        except RmapiError:
            return False

    def delete_folder(self, remote_path: str) -> None:
        """Delete an empty folder from reMarkable."""
        try:
            self._run("rm", remote_path)
            logger.info("Deleted empty folder %s", remote_path)
        except RmapiError as e:
            logger.warning("Failed to delete folder %s: %s", remote_path, e)

    def list_folder_entries(self, remote_path: str = "/") -> list[tuple[str, str]]:
        """List contents of a folder with type info.

        Returns list of (type, name) tuples where type is 'f' or 'd'.
        """
        result = self._run("ls", remote_path)
        entries = []
        for line in result.stdout.splitlines():
            match = re.match(r"^\[([fd])\]\t(.+)$", line)
            if match:
                entries.append((match.group(1), match.group(2)))
        return entries

    def list_recursive(self, remote_path: str, errors: list[str] | None = None) -> dict[str, str]:
        """Recursively list all files under a remote path.

        Returns dict mapping remote file paths to their type ('f' or 'd').
        If errors is provided, failed subfolder paths are appended to it
        so callers can detect an incomplete listing.
        """
        result: dict[str, str] = {}
        entries = self.list_folder_entries(remote_path)
        for entry_type, name in entries:
            full_path = f"{remote_path}/{name}" if remote_path != "/" else f"/{name}"
            result[full_path] = entry_type
            if entry_type == "d":
                try:
                    result.update(self.list_recursive(full_path, errors))
                except RmapiError:
                    logger.warning("Could not list %s", full_path)
                    if errors is not None:
                        errors.append(full_path)
        return result

    def download(self, remote_path: str, output_dir: Path) -> Path:
        """Download a file from reMarkable as PDF.

        Tries 'geta' first (annotated PDF for documents with a source PDF),
        falls back to 'get' (raw download) for pure notebooks.

        Returns:
            Path to the downloaded file.
        """
        name = remote_path.rsplit("/", 1)[-1]

        # Try geta first (works for annotated PDFs/ePubs)
        result = subprocess.run(
            [self._rmapi, "geta", remote_path],
            capture_output=True,
            text=True,
            cwd=str(output_dir),
        )
        if result.returncode == 0:
            found = self._find_downloaded(output_dir, name)
            if found:
                return found

        # Fall back to get (works for notebooks)
        result = subprocess.run(
            [self._rmapi, "get", remote_path],
            capture_output=True,
            text=True,
            cwd=str(output_dir),
        )
        if result.returncode != 0:
            raise RmapiError(
                f"rmapi get {remote_path} failed (exit {result.returncode}):\n{result.stderr}"
            )

        found = self._find_downloaded(output_dir, name)
        if found:
            return found

        raise RmapiError(f"Could not find downloaded file for {remote_path}")

    @staticmethod
    def _find_downloaded(output_dir: Path, name: str) -> Path | None:
        """Find a downloaded file by name in the output directory."""
        for ext in [".pdf", ".rmdoc", ".zip", ".epub"]:
            candidate = output_dir / f"{name}{ext}"
            if candidate.exists():
                return candidate
        # Fall back to any matching file in the directory
        for pattern in ["*.pdf", "*.rmdoc", "*.zip"]:
            matches = list(output_dir.glob(pattern))
            if matches:
                return matches[0]
        return None

    def stat(self, remote_path: str) -> dict[str, str]:
        """Get metadata for a remote file.

        Returns dict with keys like ID, Name, Version, ModifiedClient, Type, etc.
        """
        result = self._run("stat", remote_path)
        metadata: dict[str, str] = {}
        for line in result.stdout.splitlines():
            if ":" in line:
                key, _, value = line.partition(":")
                key = key.strip().strip('"')
                value = value.strip().rstrip(",").strip().strip('"')
                if key:
                    metadata[key] = value
        return metadata

    def replace(self, local_path: Path, remote_path: str) -> None:
        """Replace a document on reMarkable.

        Uses --force to overwrite the existing file in place.
        """
        parts = remote_path.rsplit("/", 1)
        remote_folder = parts[0] if len(parts) > 1 else "/"
        self.upload(local_path, remote_folder)
