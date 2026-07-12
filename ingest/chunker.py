"""
Produce five chunk types from parsed data:

  prose         — RecursiveCharacterTextSplitter over section bodies
  register_row  — one chunk per bit-field row from SQLite
  figure        — one chunk per extracted figure
  table_summary — one chunk per non-register table (discovery: "which table?")
  table_row     — one chunk per non-register table data row (precise fact lookup)

Output: data/parsed/chunks.jsonl  — full 10-field metadata envelope per chunk.
"""

import json
import re
import sqlite3
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter

from ingest.table_schema import make_table_id

CHUNK_SIZE = 500
CHUNK_OVERLAP = 80


def _make_citation(doc_id: str, revision: str, section_path: str, page: int) -> str:
    return f"【{doc_id} Rev.{revision} | {section_path} | p.{page}】"


# ── Prose chunks ──────────────────────────────────────────────────────────────

def _prose_chunks(
    pages_jsonl: Path,
    doc_id: str,
    revision: str,
    chip_part: str,
) -> list[dict]:
    """Group text blocks by section_path and split into overlapping chunks."""
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )

    # Group blocks by section_path
    sections: dict[str, list[dict]] = {}
    with pages_jsonl.open(encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)
            key = b.get("section_path") or "§UNKNOWN"
            sections.setdefault(key, []).append(b)

    chunks = []
    for section_path, blocks in sections.items():
        blocks.sort(key=lambda b: (b["page"], b["bbox"][1]))
        full_text = "\n".join(b["text"] for b in blocks)
        if not full_text.strip():
            continue

        page_start = blocks[0]["page"]
        page_end = blocks[-1]["page"]

        for split_text in splitter.split_text(full_text):
            render_text = f"[{section_path}] {split_text}"
            chunks.append({
                "doc_id": doc_id,
                "revision": revision,
                "chip_part": chip_part,
                "section_path": section_path,
                "page_start": page_start,
                "page_end": page_end,
                "element_type": "prose",
                "peripheral": "",
                "register_name": "",
                "figure_id": "",
                "image_path": "",
                "render_text": render_text,
                "citation": _make_citation(doc_id, revision, section_path, page_start),
            })
    return chunks


# ── Register-row chunks ───────────────────────────────────────────────────────

def _register_row_chunks(
    db_path: Path,
    doc_id: str,
    revision: str,
    chip_part: str,
) -> list[dict]:
    """One chunk per bit-field row from registers.db."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute("""
        SELECT r.peripheral, r.register_name, r.section_path, r.page_start,
               bf.bits, bf.symbol, bf.access, bf.reset, bf.description
        FROM registers r
        JOIN bit_fields bf ON r.peripheral = bf.peripheral AND r.register_name = bf.register_name
        WHERE r.doc_id = ?
    """, (doc_id,))

    chunks = []
    for row in cur.fetchall():
        section_path = row["section_path"] or "§UNKNOWN"
        render_text = (
            f"[{section_path} > {row['register_name']}] "
            f"bits {row['bits']} | {row['symbol']} | {row['access']} | "
            f"reset {row['reset']} | {row['description']}"
        )
        chunks.append({
            "doc_id": doc_id,
            "revision": revision,
            "chip_part": chip_part,
            "section_path": section_path,
            "page_start": row["page_start"],
            "page_end": row["page_start"],
            "element_type": "register_row",
            "peripheral": row["peripheral"],
            "register_name": row["register_name"],
            "figure_id": "",
            "image_path": "",
            "render_text": render_text,
            "citation": _make_citation(doc_id, revision, section_path, row["page_start"]),
        })

    con.close()
    return chunks


# ── Figure chunks ─────────────────────────────────────────────────────────────

def _figure_chunks(
    figures_jsonl: Path,
    doc_id: str,
    revision: str,
    chip_part: str,
) -> list[dict]:
    """One chunk per extracted figure."""
    chunks = []
    with figures_jsonl.open(encoding="utf-8") as f:
        for line in f:
            fig = json.loads(line)
            section_path = fig.get("section_path") or "§UNKNOWN"
            figure_id = fig.get("figure_id") or ""
            caption = fig.get("caption") or ""
            vlm_summary = fig.get("vlm_summary") or ""
            page = fig.get("page", 0)

            render_text = f"[{section_path} > {figure_id}] {caption}. {vlm_summary}"
            chunks.append({
                "doc_id": doc_id,
                "revision": revision,
                "chip_part": chip_part,
                "section_path": section_path,
                "page_start": page,
                "page_end": page,
                "element_type": "figure",
                "peripheral": "",
                "register_name": "",
                "figure_id": figure_id,
                "image_path": fig.get("image_path", ""),
                "render_text": render_text,
                "citation": _make_citation(doc_id, revision, section_path, page),
            })
    return chunks


# ── General table chunks (table_summary + table_row) ──────────────────────────

_RE_BIT_HEADER = re.compile(r"^bit\b", re.IGNORECASE)


def _table_summary_text(header: list[str], rows: list[dict], table_title: str, section_path: str) -> str:
    """1 summary sentence per table — the discovery layer ('which table?')."""
    col_list = ", ".join(header)
    title = table_title or "Untitled table"
    return f"{title} (in {section_path}) — a table with {len(rows)} row(s). Columns: {col_list}."


def _row_to_sentence(header: list[str], row: dict, table_title: str) -> str:
    """1 natural-language sentence per data row — the precise-fact layer.

    Prose form embeds better than pipe-delimited cells (see PROJECT_PLAN.md §4.4:
    0.678 vs 0.553 cosine similarity, measured on the real dataset).
    """
    parts = []
    for col in header:
        val = str(row.get(col, "") or "").replace("\n", " ").strip()
        if val:
            parts.append(f"{col} is {val}")
    if not parts:
        return ""
    title = table_title or "this table"
    return f"In {title}, " + ", ".join(parts) + "."


def _general_table_chunks(
    tables_jsonl: Path,
    doc_id: str,
    revision: str,
    chip_part: str,
) -> list[dict]:
    """table_summary (1/table) + table_row (1/data row) chunks for non-register tables."""
    chunks = []
    seen_ids: set[str] = set()
    with tables_jsonl.open(encoding="utf-8") as f:
        for line in f:
            table = json.loads(line)
            header = [str(h).strip() for h in table.get("header", []) if h]
            rows = table.get("rows", [])

            # Skip register tables — already captured in register_row chunks
            if table.get("is_register") or any(_RE_BIT_HEADER.match(h) for h in header):
                continue

            if not header or not rows:
                continue

            section_path = table.get("section_path") or "§UNKNOWN"
            table_title = table.get("table_title", "")
            page = table["page"]

            table_id = make_table_id(table_title, page, table.get("table_idx", 0))
            base_id, suffix = table_id, 1
            while table_id in seen_ids:
                table_id = f"{base_id}-{suffix}"
                suffix += 1
            seen_ids.add(table_id)

            citation = _make_citation(doc_id, revision, section_path, page)

            summary_text = _table_summary_text(header, rows, table_title, section_path)
            chunks.append({
                "doc_id": doc_id,
                "revision": revision,
                "chip_part": chip_part,
                "section_path": section_path,
                "page_start": page,
                "page_end": page,
                "element_type": "table_summary",
                "peripheral": "",
                "register_name": "",
                "figure_id": "",
                "image_path": "",
                "table_id": table_id,
                "render_text": summary_text,
                "citation": citation,
            })

            for row in rows:
                row_text = _row_to_sentence(header, row, table_title)
                if not row_text:
                    continue
                chunks.append({
                    "doc_id": doc_id,
                    "revision": revision,
                    "chip_part": chip_part,
                    "section_path": section_path,
                    "page_start": page,
                    "page_end": page,
                    "element_type": "table_row",
                    "peripheral": "",
                    "register_name": "",
                    "figure_id": "",
                    "image_path": "",
                    "table_id": table_id,
                    "render_text": row_text,
                    "citation": citation,
                })
    return chunks


# ── Main ──────────────────────────────────────────────────────────────────────

def build_chunks(
    pages_jsonl: Path,
    figures_jsonl: Path,
    tables_jsonl: Path,
    db_path: Path,
    registry_path: Path,
    output_path: Path,
) -> dict[str, int]:
    """Build all chunks and write to output_path. Returns counts by element_type."""
    registry = json.loads(registry_path.read_text())
    doc_info = registry[0]
    doc_id = doc_info["doc_id"]
    revision = doc_info["revision"]
    chip_part = doc_info["chip_part"]

    output_path.parent.mkdir(parents=True, exist_ok=True)

    all_chunks: list[dict] = []
    all_chunks += _prose_chunks(pages_jsonl, doc_id, revision, chip_part)
    all_chunks += _register_row_chunks(db_path, doc_id, revision, chip_part)
    all_chunks += _figure_chunks(figures_jsonl, doc_id, revision, chip_part)
    all_chunks += _general_table_chunks(tables_jsonl, doc_id, revision, chip_part)

    counts: dict[str, int] = {}
    with output_path.open("w", encoding="utf-8") as fout:
        for chunk in all_chunks:
            fout.write(json.dumps(chunk, ensure_ascii=False) + "\n")
            counts[chunk["element_type"]] = counts.get(chunk["element_type"], 0) + 1

    return counts


if __name__ == "__main__":
    pages_jsonl = Path("data/parsed/pages.jsonl")
    figures_jsonl = Path("data/parsed/figures.jsonl")
    tables_jsonl = Path("data/parsed/tables.jsonl")
    db_path = Path("data/store/registers.db")
    registry_path = Path("data/registry.json")
    output_path = Path("data/parsed/chunks.jsonl")

    print("Building chunks ...")
    counts = build_chunks(pages_jsonl, figures_jsonl, tables_jsonl, db_path, registry_path, output_path)

    total = sum(counts.values())
    print(f"\nTotal chunks: {total}")
    for etype, n in sorted(counts.items()):
        print(f"  {etype}: {n}")

    # Verify all three types present
    missing = [t for t in ("prose", "register_row", "figure", "table_summary", "table_row") if t not in counts]
    if missing:
        print(f"\nWARNING: missing element types: {missing}")
    else:
        print("\nCheckpoint: all five element types present ✓")
