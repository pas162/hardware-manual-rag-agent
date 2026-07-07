"""
Produce three chunk types from Smart-Manual-parsed data:

  prose  — RecursiveCharacterTextSplitter over freeWord prose (pages_sm.jsonl)
  table  — one chunk per preserved general/lookup table (tables_sm.jsonl)
  figure — one chunk per figure discovery record (figures_sm.jsonl)

Registers are not pre-indexed here — register_lookup queries the Smart Manual
DB live. Output: data/parsed/chunks.jsonl, one metadata envelope per chunk.
"""

import json
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80
_ROWS_PER_CHUNK = 40


def _make_citation(chip_part: str, section_title: str, figure_id: str = "") -> str:
    if figure_id:
        return f"【{chip_part} Smart Manual | {section_title} | {figure_id}】"
    return f"【{chip_part} Smart Manual | {section_title}】"


# ── Prose chunks ──────────────────────────────────────────────────────────────

def _prose_chunks(pages_jsonl: Path, chip_part: str) -> list[dict]:
    """Split each freeWord section's cleaned prose into overlapping chunks."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    chunks = []
    with pages_jsonl.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            section_title = row["section_title"]
            text = row["text"]
            if not text.strip():
                continue

            for split_text in splitter.split_text(text):
                render_text = f"[{section_title}] {split_text}"
                chunks.append({
                    "chip_part": chip_part,
                    "section_title": section_title,
                    "element_type": "prose",
                    "figure_id": "",
                    "render_text": render_text,
                    "citation": _make_citation(chip_part, section_title),
                })
    return chunks


# ── Table chunks ──────────────────────────────────────────────────────────────

def _table_chunks(tables_jsonl: Path, chip_part: str) -> list[dict]:
    """One chunk per preserved general/lookup table; split large tables into batches."""
    chunks = []
    with tables_jsonl.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            section_title = row["section_title"]
            table_title = row.get("table_title", "")
            rows_text = row["rows_text"]
            if not rows_text.strip():
                continue

            lines = rows_text.split("\n")
            for i in range(0, len(lines), _ROWS_PER_CHUNK):
                batch = lines[i:i + _ROWS_PER_CHUNK]
                body = "\n".join(batch)
                if not body.strip():
                    continue
                prefix = f"[{section_title}]"
                if table_title:
                    prefix += f"\n{table_title}"
                render_text = f"{prefix}\n{body}"
                chunks.append({
                    "chip_part": chip_part,
                    "section_title": section_title,
                    "element_type": "table",
                    "figure_id": "",
                    "render_text": render_text,
                    "citation": _make_citation(chip_part, section_title),
                })
    return chunks


# ── Figure chunks ─────────────────────────────────────────────────────────────

def _figure_chunks(figures_jsonl: Path, chip_part: str) -> list[dict]:
    """One chunk per figure discovery record."""
    chunks = []
    with figures_jsonl.open(encoding="utf-8") as f:
        for line in f:
            row = json.loads(line)
            section_title = row["section_title"]
            figure_id = row["figure_id"]
            caption = row.get("caption", "")

            render_text = f"[{section_title} > {figure_id}] {caption}"
            chunks.append({
                "chip_part": chip_part,
                "section_title": section_title,
                "element_type": "figure",
                "figure_id": figure_id,
                "render_text": render_text,
                "citation": _make_citation(chip_part, section_title, figure_id),
            })
    return chunks


# ── Main ──────────────────────────────────────────────────────────────────────

def build_chunks(
    pages_jsonl: Path,
    tables_jsonl: Path,
    figures_jsonl: Path,
    chip_part: str,
    output_path: Path,
) -> dict[str, int]:
    """Build all chunks and write to output_path. Returns counts by element_type."""
    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict] = []
    all_chunks += _prose_chunks(pages_jsonl, chip_part)
    all_chunks += _table_chunks(tables_jsonl, chip_part)
    all_chunks += _figure_chunks(figures_jsonl, chip_part)

    counts: dict[str, int] = {}
    with output_path.open("w", encoding="utf-8") as fout:
        for chunk in all_chunks:
            fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            counts[chunk["element_type"]] = counts.get(chunk["element_type"], 0) + 1

    return counts


if __name__ == "__main__":
    import sys

    chip = sys.argv[1] if len(sys.argv) > 1 else "RA6M4"

    pages_jsonl = Path("data/parsed/pages_sm.jsonl")
    tables_jsonl = Path("data/parsed/tables_sm.jsonl")
    figures_jsonl = Path("data/parsed/figures_sm.jsonl")
    output_path = Path("data/parsed/chunks.jsonl")

    print("Building chunks ...")
    counts = build_chunks(pages_jsonl, tables_jsonl, figures_jsonl, chip, output_path)

    total = sum(counts.values())
    print(f"\nTotal chunks: {total}")
    for etype, n in sorted(counts.items()):
        print(f"  {etype}: {n}")

    missing = [t for t in ("prose", "table", "figure") if t not in counts]
    if missing:
        print(f"\nWARNING: missing element types: {missing}")
    else:
        print("\nCheckpoint: all three element types present")
