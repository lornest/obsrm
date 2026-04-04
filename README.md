# obsrm

Bidirectional sync between an Obsidian vault and a reMarkable tablet. Pushes markdown notes as ePub/PDF (preserving wikilinks, callouts, embeds, images) and pulls notebooks/handwritten notes back as markdown. Runs locally via CLI or automatically via GitHub Actions.

## How it works

```
Push (Obsidian -> reMarkable):
  Detect changed files (SHA-256 hash-based)
  -> Preprocess Obsidian markdown (embeds, frontmatter, dataview)
  -> Convert to ePub/PDF via Pandoc
  -> Upload to reMarkable Cloud via rmapi

Pull (reMarkable -> Obsidian):
  List remote files via rmapi
  -> Detect new/changed files (ModifiedClient timestamp)
  -> Download notebooks and annotated PDFs
  -> Extract typed text or render handwriting as SVG
  -> Create markdown files in vault
```

Obsidian is the source of truth. Files created on either side are tracked and synced incrementally via `.sync-state.json`.

## Quick start

```bash
# Install prerequisites
brew install pandoc uv
# Download rmapi from https://github.com/ddvk/rmapi/releases

# Clone and install
git clone <repo-url> && cd obsrm
uv sync

# Authenticate with reMarkable Cloud
uv run obsrm auth

# Preview what would be synced
uv run obsrm sync --vault-path ~/my-vault --dry-run

# Sync for real
uv run obsrm sync --vault-path ~/my-vault
```

## CLI commands

```
obsrm sync [OPTIONS]    Bidirectional sync (push then pull)
  --vault-path PATH    Path to Obsidian vault (default: current dir)
  --config PATH        Path to sync-config.yaml
  --dry-run            Show what would change without syncing
  --force              Re-sync all files regardless of state
  -v, --verbose        Debug logging

obsrm pull [OPTIONS]    Pull-only (reMarkable -> Obsidian)
  --vault-path PATH    Path to Obsidian vault (default: current dir)
  --config PATH        Path to sync-config.yaml
  --dry-run            Show what would be pulled without doing it

obsrm auth              Set up reMarkable Cloud auth
obsrm status [OPTIONS]  Show sync state and pending changes
```

## Configuration

Create `sync-config.yaml` in your vault root:

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

sync:
  state_file: ".sync-state.json"
  delete_removed: false         # Delete from reMarkable when removed from vault
  flatten: false                # Flatten folder structure on reMarkable

pull:
  attachments_folder: "attachments"  # Where to store downloaded PDFs/SVGs
```

All settings have sensible defaults. The config file is optional.

## Obsidian features supported

- **Wikilinks** — `[[page]]` and `[[page|display text]]`
- **Embeds/transclusions** — `![[note]]` and `![[note#heading]]` inlined recursively
- **Callouts** — `> [!NOTE]`, `> [!WARNING]`, foldable variants
- **Highlights** — `==highlighted text==` converted to emphasis
- **Images** — `![[image.png]]` and standard `![alt](path)` resolved from anywhere in vault
- **Frontmatter** — stripped, title extracted for document metadata
- **Dataview** — code blocks removed (dynamic queries can't be resolved statically)

## GitHub Actions

Push-to-sync: changes to `.md` files or config on `main` automatically sync to your reMarkable.

1. Run `uv run obsrm auth` locally
2. Add contents of `~/.rmapi/rmapi.conf` as GitHub secret `RMAPI_CONFIG`
3. Copy [`examples/sync-remarkable.yaml`](examples/sync-remarkable.yaml) to `.github/workflows/` in your vault repo

Manual triggers support `--force` and `--dry-run` via the Actions UI.

See [SETUP.md](SETUP.md) for detailed instructions.

## Architecture

```
src/obsrm/
  cli.py                 Click CLI entry point (sync, pull, status, auth)
  config.py              YAML config + pydantic validation
  vault.py               Vault scanning with include/exclude globs
  sync_state.py          JSON state file and changeset computation
  markdown_processor.py  Obsidian markdown preprocessing
  converter.py           Pandoc ePub/PDF conversion
  pull.py                Reverse sync (download + extract from reMarkable)
  rm_extract.py          Extract typed text from reMarkable notebooks
  remarkable.py          rmapi CLI wrapper
filters/obsidian.lua     Pandoc Lua filter (callouts, highlights)
styles/remarkable.css    E-ink optimized stylesheet
```

## Dependencies

- **Python 3.14+**
- **[Pandoc](https://pandoc.org/)** 3.0+ — document conversion
- **[rmapi](https://github.com/ddvk/rmapi)** — reMarkable Cloud CLI (ddvk fork)
