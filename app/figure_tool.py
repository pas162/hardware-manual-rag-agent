"""
get_figure(figure_id, chip_part) — retrieve a figure record from ChromaDB.
"""

import re
from pathlib import Path

from app.store import get_vectorstore, get_registry

_ROOT = Path(__file__).resolve().parent.parent


def get_figure(figure_id: str, chip_part: str) -> dict | None:
    """Retrieve a figure by its ID (e.g. 'Figure 13.2').

    Returns a FigureRecord dict, or None if not found.
    """
    vs = get_vectorstore()

    # Filter by both figure_id and chip_part
    results = vs.get(
        where={"$and": [{"figure_id": {"$eq": figure_id}}, {"chip_part": {"$eq": chip_part}}]},
        include=["documents", "metadatas"],
    )

    if not results or not results.get("ids"):
        return None

    # Take first match
    doc_text = results["documents"][0] if results.get("documents") else ""
    meta = results["metadatas"][0] if results.get("metadatas") else {}

    registry = get_registry()
    doc_info = registry.get(chip_part)
    revision = doc_info["revision"] if doc_info else ""
    doc_id = doc_info["doc_id"] if doc_info else ""

    section_path = meta.get("section_path", "§UNKNOWN")
    page = meta.get("page_start", 0)
    image_path = meta.get("image_path", "")
    citation = meta.get("citation") or f"【{doc_id} Rev.{revision} | {section_path} | p.{page} | {figure_id}】"

    # Extract caption and VLM summary from render_text
    # render_text format: "[section_path > figure_id] caption. vlm_summary"
    caption = ""
    vlm_summary = ""
    if doc_text:
        m = re.match(r"^\[.*?\]\s*(.+?)(?:\.\s+(.+))?$", doc_text, re.DOTALL)
        if m:
            caption = m.group(1).strip()
            vlm_summary = m.group(2).strip() if m.group(2) else ""

    return {
        "figure_id": figure_id,
        "caption": caption,
        "vlm_summary": vlm_summary,
        "image_path": image_path,
        "section_path": section_path,
        "page": page,
        "citation": citation,
    }


if __name__ == "__main__":
    import sys
    import json

    fid = sys.argv[1] if len(sys.argv) > 1 else "Figure 13.2"
    chip = sys.argv[2] if len(sys.argv) > 2 else "RA6M4"

    result = get_figure(fid, chip)
    if result:
        print(json.dumps(result, indent=2))
    else:
        print(f"Figure {fid!r} not found for chip {chip}")