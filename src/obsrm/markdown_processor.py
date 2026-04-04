"""Obsidian-specific markdown preprocessing before Pandoc conversion."""

import re
from pathlib import Path


def process_markdown(
    content: str, file_path: Path, vault_path: Path
) -> tuple[str, str | None, list[tuple[Path, str]]]:
    """Preprocess Obsidian markdown for Pandoc consumption.

    Handles:
    - YAML frontmatter extraction (returns title if found)
    - Dataview code block removal
    - Obsidian image embed conversion (![[image.png]] -> standard markdown)
    - Note embed transclusion (![[note]] -> inlined content)
    - Standard markdown image path resolution (![alt](path))

    Args:
        content: raw markdown content
        file_path: absolute path to the source file
        vault_path: root path of the vault (for resolving embeds)

    Returns:
        Tuple of (processed_markdown, title_or_none, images) where images
        is a list of (source_path, reference_name) tuples for files that
        need to be staged for Pandoc.
    """
    title, content = _extract_frontmatter(content)
    content = _strip_dataview_blocks(content)
    content = _resolve_embeds(content, vault_path, seen=set())
    images: list[tuple[Path, str]] = []
    content = _convert_image_embeds(content, file_path, vault_path, images)
    content = _resolve_standard_images(content, file_path, vault_path, images)
    return content, title, images


def _extract_frontmatter(content: str) -> tuple[str | None, str]:
    """Strip YAML frontmatter and extract title if present.

    Returns (title, content_without_frontmatter).
    """
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n", content, re.DOTALL)
    if not match:
        return None, content

    frontmatter = match.group(1)
    content_without_fm = content[match.end() :]

    # Try to extract title from frontmatter
    title = None
    for line in frontmatter.splitlines():
        m = re.match(r"^title:\s*(.+)$", line.strip())
        if m:
            title = m.group(1).strip().strip("\"'")
            break

    return title, content_without_fm


def _strip_dataview_blocks(content: str) -> str:
    """Remove ```dataview ... ``` and ```dataviewjs ... ``` code blocks."""
    return re.sub(
        r"```dataview(?:js)?\s*\n.*?\n```",
        "",
        content,
        flags=re.DOTALL,
    )


def _resolve_embeds(content: str, vault_path: Path, seen: set[str], depth: int = 0) -> str:
    """Resolve ![[note]] transclusions by inlining referenced note content.

    Cycle detection via `seen` set. Max depth of 10 to prevent runaway recursion.
    """
    if depth > 10:
        return content

    def replace_embed(match: re.Match) -> str:
        ref = match.group(1).strip()

        # Skip image embeds (handled separately)
        if _is_image_ref(ref):
            return match.group(0)

        # Handle heading references like ![[note#heading]]
        heading = None
        if "#" in ref:
            ref, heading = ref.split("#", 1)

        # Resolve the file path
        resolved = _find_vault_file(ref, vault_path)
        if resolved is None:
            return f"> *Could not resolve embed: {match.group(1)}*"

        rel = resolved.relative_to(vault_path).as_posix()
        if rel in seen:
            return f"> *Circular embed: {match.group(1)}*"

        seen.add(rel)
        try:
            embedded_content = resolved.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            seen.discard(rel)
            return f"> *Could not read embed (encoding error): {match.group(1)}*"

        # Strip frontmatter from embedded content
        _, embedded_content = _extract_frontmatter(embedded_content)

        # If heading reference, extract only that section
        if heading:
            embedded_content = _extract_section(embedded_content, heading)

        # Recursively resolve embeds in the inlined content
        embedded_content = _resolve_embeds(embedded_content, vault_path, seen, depth + 1)
        seen.discard(rel)

        return embedded_content

    return re.sub(r"!\[\[([^\]]+)\]\]", replace_embed, content)


def _extract_section(content: str, heading: str) -> str:
    """Extract content under a specific heading."""
    lines = content.splitlines(keepends=True)
    capturing = False
    captured: list[str] = []
    target_level = 0

    for line in lines:
        heading_match = re.match(r"^(#{1,6})\s+(.+)$", line.rstrip())
        if heading_match:
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            if not capturing and title.lower() == heading.lower():
                capturing = True
                target_level = level
                captured.append(line)
                continue
            elif capturing and level <= target_level:
                break
        if capturing:
            captured.append(line)

    return "".join(captured) if captured else f"> *Section not found: {heading}*\n"


def _convert_image_embeds(
    content: str,
    file_path: Path,
    vault_path: Path,
    images: list[tuple[Path, str]],
) -> str:
    """Convert ![[image.png]] to standard markdown with staged image refs.

    Resolved images are added to the `images` list so the converter can
    copy them to a staging directory alongside the preprocessed markdown.
    References in the output markdown use just the filename so Pandoc can
    find them via --resource-path.
    """

    def replace_image(match: re.Match) -> str:
        ref = match.group(1).strip()
        if not _is_image_ref(ref):
            return match.group(0)

        # Handle size syntax ![[image.png|300]]
        display = ""
        if "|" in ref:
            ref, display = ref.rsplit("|", 1)

        resolved = _find_vault_file(ref, vault_path)
        if resolved is None:
            return match.group(0)

        # Use the filename as the reference — converter will stage the file
        staged_name = resolved.name
        images.append((resolved, staged_name))
        return f"![{display}]({staged_name})"

    return re.sub(r"!\[\[([^\]]+)\]\]", replace_image, content)


def _resolve_standard_images(
    content: str,
    file_path: Path,
    vault_path: Path,
    images: list[tuple[Path, str]],
) -> str:
    """Resolve standard markdown image references ![alt](path) to staged paths.

    Handles relative paths that may reference images elsewhere in the vault.
    """

    def replace_image(match: re.Match) -> str:
        alt = match.group(1)
        img_path = match.group(2)

        # Skip URLs
        if img_path.startswith(("http://", "https://", "data:")):
            return match.group(0)

        # Try to resolve relative to the file first, then vault root
        candidates = [
            file_path.parent / img_path,
            vault_path / img_path,
        ]
        for candidate in candidates:
            if candidate.exists():
                staged_name = candidate.name
                images.append((candidate, staged_name))
                return f"![{alt}]({staged_name})"

        return match.group(0)

    return re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", replace_image, content)


def _is_image_ref(ref: str) -> bool:
    """Check if a reference points to an image file."""
    # Strip size suffix
    name = ref.rsplit("|", 1)[0].strip()
    return Path(name).suffix.lower() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".webp",
        ".svg",
        ".bmp",
    }


def _find_vault_file(ref: str, vault_path: Path) -> Path | None:
    """Find a file in the vault by its Obsidian reference name.

    Obsidian allows referencing files by just their name (without path).
    We search for exact path first, then fall back to name-based search.
    """
    # Try exact relative path first
    candidate = vault_path / ref
    if candidate.exists():
        return candidate

    # Add .md extension if missing
    if not Path(ref).suffix:
        candidate = vault_path / f"{ref}.md"
        if candidate.exists():
            return candidate

    # Search by filename anywhere in vault
    name = Path(ref).name
    if not Path(name).suffix:
        name = f"{name}.md"

    matches = list(vault_path.rglob(name))
    if len(matches) == 1:
        return matches[0]
    elif len(matches) > 1:
        # Prefer shortest path (closest to root)
        return min(matches, key=lambda p: len(p.parts))

    return None
