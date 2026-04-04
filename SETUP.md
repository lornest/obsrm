# Setup Guide

## Prerequisites

- **Python 3.14+**
- **[uv](https://docs.astral.sh/uv/)** — Python package manager
- **[Pandoc](https://pandoc.org/installing.html)** 3.0+ — document conversion
- **[rmapi](https://github.com/ddvk/rmapi/releases)** — reMarkable Cloud CLI

### Install on macOS

```bash
brew install pandoc uv

# rmapi (Apple Silicon)
mkdir -p ~/.local/bin
curl -sL https://github.com/ddvk/rmapi/releases/download/v0.0.32/rmapi-macos-arm64.zip -o /tmp/rmapi.zip
unzip -o /tmp/rmapi.zip -d ~/.local/bin/
chmod +x ~/.local/bin/rmapi
rm /tmp/rmapi.zip
```

### Install on Ubuntu/Debian

```bash
sudo apt-get install pandoc
# uv: https://docs.astral.sh/uv/getting-started/installation/
# rmapi: download from https://github.com/ddvk/rmapi/releases
```

## Local Setup

### 1. Clone and install

```bash
git clone <your-repo-url>
cd obsrm
uv sync
```

### 2. Authenticate with reMarkable Cloud

```bash
uv run obsrm auth
```

This runs `rmapi` which will prompt you to:

1. Visit https://my.remarkable.com/device/browser/connect
2. Enter the one-time code displayed in your terminal

Your credentials are stored in `~/.rmapi/rmapi.conf`.

### 3. Configure your vault

Create a `sync-config.yaml` in your Obsidian vault root:

```yaml
remarkable:
  target_folder: "/Obsidian"    # Folder on reMarkable
  format: "epub"                # "epub" or "pdf"

vault:
  include:
    - "**/*.md"
  exclude:
    - "_templates/**"
    - ".obsidian/**"
    - "daily-notes/**"          # Customize as needed

sync:
  state_file: ".sync-state.json"
  delete_removed: false         # Set true to delete from reMarkable when removed from vault
  flatten: false                # Set true to put all files in one folder (no subfolders)
```

If no config file exists, defaults are used (all `.md` files, excluding `.obsidian/`).

### 4. Run a sync

```bash
# Preview what would be synced
uv run obsrm sync --vault-path ~/path/to/vault --dry-run

# Sync for real
uv run obsrm sync --vault-path ~/path/to/vault

# Force re-sync everything
uv run obsrm sync --vault-path ~/path/to/vault --force

# Check current sync status
uv run obsrm status --vault-path ~/path/to/vault
```

## GitHub Actions Setup

This enables automatic syncing to your reMarkable whenever you push changes to your vault repo.

### 1. Get your rmapi token

After authenticating locally (step 2 above), your token is stored at `~/.rmapi/rmapi.conf`. View it:

```bash
cat ~/.rmapi/rmapi.conf
```

The file contents look something like:

```
devicetoken: <long-token-string>
usertoken: <long-token-string>
```

### 2. Add the secret to your GitHub repo

1. Go to your vault repository on GitHub
2. Navigate to **Settings** > **Secrets and variables** > **Actions**
3. Click **New repository secret**
4. Name: `RMAPI_CONFIG`
5. Value: paste the entire contents of `~/.rmapi/rmapi.conf`
6. Click **Add secret**

### 3. Add the workflow files

Copy `examples/sync-remarkable.yaml` from this repo to `.github/workflows/` in your vault repository. Adjust the `--vault-path` if your vault is in a subdirectory.

The sync workflow will:
- Trigger on pushes to `main` that change `.md` files or `sync-config.yaml`
- Convert changed notes to ePub and upload to reMarkable
- Commit the updated `.sync-state.json` back to the repo
- Can also be triggered manually via the Actions tab (with force/dry-run options)

The CI workflow in this tool's repo runs tests — you don't need it in your vault repo.

### 4. Disable syncing (optional)

To temporarily disable the sync without removing the workflow:

1. Go to **Settings** > **Variables** > **Actions**
2. Add a variable: `SYNC_ENABLED` = `false`

Remove the variable or set it to `true` to re-enable.

## Token Refresh

reMarkable device tokens can expire. If syncing fails with an authentication error:

1. Re-run `uv run obsrm auth` locally
2. Update the `RMAPI_CONFIG` secret in GitHub with the new `~/.rmapi/rmapi.conf` contents

## Environment Variables

These override config file values (useful in CI):

| Variable | Description |
|----------|-------------|
| `VAULT_PATH` | Path to the vault (alternative to `--vault-path`) |
| `REMARKABLE_TARGET_FOLDER` | Override target folder on reMarkable |
| `REMARKABLE_FORMAT` | Override output format (`epub` or `pdf`) |
