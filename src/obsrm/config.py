"""Configuration loading for obsrm."""

import os
from typing import Literal

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class RemarkableConfig(BaseModel):
    target_folder: str = "/Obsidian"
    format: Literal["epub", "pdf"] = "epub"


class VaultConfig(BaseModel):
    include: list[str] = Field(default_factory=lambda: ["**/*.md"])
    exclude: list[str] = Field(default_factory=lambda: ["_templates/**"])


class SyncConfig(BaseModel):
    state_file: str = ".sync-state.json"
    delete_removed: bool = False
    flatten: bool = False


class PullConfig(BaseModel):
    attachments_folder: str = "attachments"


class Config(BaseModel):
    remarkable: RemarkableConfig = Field(default_factory=RemarkableConfig)
    vault: VaultConfig = Field(default_factory=VaultConfig)
    sync: SyncConfig = Field(default_factory=SyncConfig)
    pull: PullConfig = Field(default_factory=PullConfig)


def load_config(vault_path: Path, config_path: Path | None = None) -> Config:
    """Load configuration from sync-config.yaml in the vault root.

    Environment variable overrides:
        VAULT_PATH: override vault path (handled by CLI)
        REMARKABLE_TARGET_FOLDER: override target folder on reMarkable
        REMARKABLE_FORMAT: override output format (epub/pdf)
    """
    if config_path is None:
        config_path = vault_path / "sync-config.yaml"

    if config_path.exists():
        with open(config_path) as f:
            data = yaml.safe_load(f) or {}
    else:
        data = {}

    config = Config.model_validate(data)

    # Environment variable overrides
    if env_folder := os.environ.get("REMARKABLE_TARGET_FOLDER"):
        config.remarkable.target_folder = env_folder
    if env_format := os.environ.get("REMARKABLE_FORMAT"):
        if env_format not in ("epub", "pdf"):
            raise ValueError(
                f"Invalid REMARKABLE_FORMAT={env_format!r}. Must be 'epub' or 'pdf'."
            )
        config.remarkable.format = env_format

    return config
