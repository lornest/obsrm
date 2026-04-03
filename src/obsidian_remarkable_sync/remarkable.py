"""reMarkable Cloud interface via rmapi CLI."""

import logging
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
                "rmapi is not installed. "
                "Install from https://github.com/ddvk/rmapi/releases"
            )

    def _run(self, *args: str) -> subprocess.CompletedProcess[str]:
        cmd = [self._rmapi, *args]
        logger.debug("Running: %s", " ".join(cmd))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RmapiError(
                f"rmapi {' '.join(args)} failed (exit {result.returncode}):\n"
                f"{result.stderr}"
            )
        return result

    def ensure_folder(self, remote_path: str) -> None:
        """Create folder hierarchy on reMarkable, creating parents as needed."""
        parts = [p for p in remote_path.strip("/").split("/") if p]
        current = ""
        for part in parts:
            current = f"{current}/{part}"
            try:
                self._run("mkdir", current)
            except RmapiError:
                # Folder likely already exists, which is fine
                pass

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
        """Delete a document from reMarkable."""
        try:
            self._run("rm", remote_path)
            logger.info("Deleted %s", remote_path)
        except RmapiError as e:
            logger.warning("Failed to delete %s: %s", remote_path, e)

    def list_folder(self, remote_path: str = "/") -> list[str]:
        """List contents of a folder on reMarkable."""
        result = self._run("ls", remote_path)
        return [
            line.strip()
            for line in result.stdout.splitlines()
            if line.strip()
        ]

    def replace(self, local_path: Path, remote_path: str) -> None:
        """Replace a document on reMarkable.

        Uses --force to overwrite the existing file in place.
        """
        parts = remote_path.rsplit("/", 1)
        remote_folder = parts[0] if len(parts) > 1 else "/"
        self.upload(local_path, remote_folder)
