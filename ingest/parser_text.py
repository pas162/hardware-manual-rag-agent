"""
Parse text blocks and TOC from a PDF UM; assign section_path to every block.

Output: data/parsed/pages.jsonl  — one JSON object per text block:
  {"page": 51, "bbox": [...], "text": "...", "section_path": "§13 > §13.2"}
"""

import json
import re
import sys
from pathlib import Path

import pymupdf  # fitz


def _build_toc_index(toc: list) -> list[tuple[int, str]]:
    """Return list of (page_0indexed, section_label) sorted by page, built from PyMuPDF TOC.

    PyMuPDF TOC rows: [level, title, page_1indexed]
    """
    entries: list[tuple[int, str]] = []
    # Track hierarchy path keyed by level
    current: dict[int, str] = {}

    for level, title, page in toc:
        # Normalise title: strip leading numbers like "13.2.4  Some Title"
        clean = title.strip()
        current[level] = clean
        # Drop any levels deeper than current
        for k in list(current.keys()):
            if k > level:
                del current[k]

        path_parts = [current[k] for k in sorted(current.keys())]
        section_label = " > ".join(f"§{p}" for p in path_parts)
        entries.append((page - 1, section_label))  # convert to 0-indexed

    # Sort by page so bisect works
    entries.sort(key=lambda x: x[0])
    return entries


def _resolve_section(toc_index: list[tuple[int, str]], page_0: int) -> str | None:
    """Return the deepest TOC section whose page <= page_0."""
    if not toc_index:
        return None
    result = None
    for page, label in toc_index:
        if page <= page_0:
            result = label
        else:
            break
    return result


def parse_text(
    pdf_path: str | Path, output_path: str | Path, doc_id: str = "", mode: str = "w"
) -> int:
    """Parse all text blocks from *pdf_path* and write JSONL to *output_path*.

    doc_id is stamped onto every record so multiple documents can share the
    same output file (see mode="a") and still be filtered apart downstream.
    mode="a" appends to an existing file instead of overwriting it — used by
    run_all.py when ingesting more than one document into shared intermediates.

    Returns number of blocks written.
    """
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    doc = pymupdf.open(str(pdf_path))
    toc = doc.get_toc()
    toc_index = _build_toc_index(toc)

    count = 0
    with output_path.open(mode, encoding="utf-8") as fout:
        for page_num in range(len(doc)):
            page = doc[page_num]
            blocks = page.get_text("blocks")  # list of (x0,y0,x1,y1,text,block_no,block_type)
            section_path = _resolve_section(toc_index, page_num)
            for block in blocks:
                x0, y0, x1, y1, text, block_no, block_type = block
                if block_type != 0:  # 0 = text, 1 = image
                    continue
                text = text.strip()
                if not text:
                    continue
                record = {
                    "doc_id": doc_id,
                    "page": page_num + 1,  # 1-indexed for humans
                    "bbox": [round(x0, 1), round(y0, 1), round(x1, 1), round(y1, 1)],
                    "text": text,
                    "section_path": section_path,
                }
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

    doc.close()
    return count


if __name__ == "__main__":
    import random

    registry_path = Path("data/registry.json")
    registry = json.loads(registry_path.read_text())
    doc_info = registry[0]

    pdf_path = Path(doc_info["path"])
    output_path = Path("data/parsed/pages.jsonl")

    print(f"Parsing {pdf_path} ...")
    n = parse_text(pdf_path, output_path, doc_id=doc_info["doc_id"])
    print(f"Wrote {n} text blocks to {output_path}")

    # Checkpoint: sample blocks on pages 50-100
    blocks_50_100 = []
    with output_path.open(encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)
            if 50 <= b["page"] <= 100:
                blocks_50_100.append(b)

    with_section = [b for b in blocks_50_100 if b["section_path"]]
    if blocks_50_100:
        pct = len(with_section) / len(blocks_50_100) * 100
        print(f"Pages 50-100: {len(blocks_50_100)} blocks, {pct:.1f}% have section_path")
    else:
        print("No blocks found on pages 50-100")

    if blocks_50_100:
        samples = random.sample(blocks_50_100, min(3, len(blocks_50_100)))
        print("\nRandom samples:")
        for s in samples:
            print(f"  p{s['page']} [{s['section_path']}] {s['text'][:80]!r}")
