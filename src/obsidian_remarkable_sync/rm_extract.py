"""Extract text content from reMarkable .rm files via rmscene."""

import zipfile
from pathlib import Path


def extract_text_from_rmdoc(rmdoc_path: Path) -> list[dict]:
    """Extract text content from an .rmdoc file.

    Returns list of page dicts, each with a list of paragraphs.
    Each paragraph has 'text' and 'style' (heading/bold/plain).
    """
    pages = []
    with zipfile.ZipFile(rmdoc_path) as zf:
        rm_files = sorted(name for name in zf.namelist() if name.endswith(".rm"))
        for rm_name in rm_files:
            with zf.open(rm_name) as f:
                page = _extract_page_text(f)
                if page["paragraphs"]:
                    pages.append(page)
    return pages


def _extract_page_text(f) -> dict:
    """Extract text from a single .rm file handle."""
    from rmscene import RootTextBlock, read_blocks
    from rmscene.scene_items import ParagraphStyle

    paragraphs = []

    try:
        blocks = list(read_blocks(f))
    except Exception:
        return {"paragraphs": []}

    for block in blocks:
        if not isinstance(block, RootTextBlock):
            continue

        text_obj = block.value
        if text_obj is None:
            continue

        # Extract the full text from CRDT sequence items
        full_text = ""
        for item in text_obj.items.sequence_items():
            if hasattr(item, "value") and isinstance(item.value, str):
                full_text += item.value

        if not full_text.strip():
            continue

        # Get paragraph styles
        styles = {}
        if hasattr(text_obj, "styles") and text_obj.styles:
            for crdt_id, lww in text_obj.styles.items():
                styles[crdt_id] = lww.value

        # Determine the dominant style
        style = "plain"
        for s in styles.values():
            if s == ParagraphStyle.HEADING:
                style = "heading"
                break

        # Split into paragraphs
        for line in full_text.split("\n"):
            line = line.strip()
            if line:
                paragraphs.append({"text": line, "style": style})
                # Only first paragraph gets the heading style
                style = "plain"

    return {"paragraphs": paragraphs}


def pages_to_markdown(pages: list[dict], title: str) -> str:
    """Convert extracted pages to markdown."""
    lines = []

    for page_idx, page in enumerate(pages):
        if page_idx > 0:
            lines.append("")
            lines.append("---")
            lines.append("")

        for para in page["paragraphs"]:
            style = para.get("style", "plain")
            text = para["text"]
            if style == "heading":
                lines.append(f"# {text}")
            elif style == "bold":
                lines.append(f"**{text}**")
            else:
                lines.append(text)
            lines.append("")

    return "\n".join(lines)
