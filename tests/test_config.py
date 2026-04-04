"""Tests for config loading and environment variable overrides."""

from obsrm.config import load_config


def test_defaults_when_no_config_file(tmp_path):
    config = load_config(tmp_path)
    assert config.remarkable.target_folder == "/Obsidian"
    assert config.remarkable.format == "epub"
    assert config.vault.include == ["**/*.md"]
    assert config.vault.exclude == ["_templates/**"]
    assert config.sync.state_file == ".sync-state.json"
    assert config.sync.delete_removed is False
    assert config.sync.flatten is False
    assert config.pull.attachments_folder == "attachments"


def test_load_from_yaml(tmp_path):
    config_file = tmp_path / "sync-config.yaml"
    config_file.write_text(
        "remarkable:\n"
        "  target_folder: /MyFolder\n"
        "  format: pdf\n"
        "sync:\n"
        "  delete_removed: true\n"
        "  flatten: true\n"
    )
    config = load_config(tmp_path)
    assert config.remarkable.target_folder == "/MyFolder"
    assert config.remarkable.format == "pdf"
    assert config.sync.delete_removed is True
    assert config.sync.flatten is True


def test_explicit_config_path(tmp_path):
    custom = tmp_path / "custom.yaml"
    custom.write_text("remarkable:\n  format: pdf\n")
    config = load_config(tmp_path, config_path=custom)
    assert config.remarkable.format == "pdf"


def test_env_override_target_folder(tmp_path, monkeypatch):
    monkeypatch.setenv("REMARKABLE_TARGET_FOLDER", "/EnvFolder")
    config = load_config(tmp_path)
    assert config.remarkable.target_folder == "/EnvFolder"


def test_env_override_format(tmp_path, monkeypatch):
    monkeypatch.setenv("REMARKABLE_FORMAT", "pdf")
    config = load_config(tmp_path)
    assert config.remarkable.format == "pdf"


def test_env_overrides_yaml(tmp_path, monkeypatch):
    config_file = tmp_path / "sync-config.yaml"
    config_file.write_text("remarkable:\n  target_folder: /FromYaml\n  format: epub\n")
    monkeypatch.setenv("REMARKABLE_TARGET_FOLDER", "/FromEnv")
    config = load_config(tmp_path)
    assert config.remarkable.target_folder == "/FromEnv"


def test_empty_yaml_file(tmp_path):
    config_file = tmp_path / "sync-config.yaml"
    config_file.write_text("")
    config = load_config(tmp_path)
    assert config.remarkable.target_folder == "/Obsidian"
