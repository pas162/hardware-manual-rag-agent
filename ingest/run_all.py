"""
Full ingestion pipeline runner.

Run from project root:
  python -m ingest.run_all              # full pipeline, chip=RA6M4
  python -m ingest.run_all RA6M4        # explicit chip_part
  python -m ingest.run_all --skip-embed # skip ChromaDB indexing

Steps:
  1. parser_smart_manual_text    -> data/parsed/pages_sm.jsonl, tables_sm.jsonl
  2. parser_smart_manual_figures -> data/parsed/figures_sm.jsonl
  3. chunker                     -> data/parsed/chunks.jsonl
  4. indexer                     -> data/store/chroma/

Registers are not part of this pipeline — register_lookup queries the Smart
Manual DB live (see app/register_tool.py).
"""

import sys
import time
from pathlib import Path

from app.smart_manual_locator import locate
from ingest.parser_smart_manual_text import parse_freewords
from ingest.parser_smart_manual_figures import parse_figures
from ingest.chunker import build_chunks
from ingest.indexer import build_index


def _step(label: str):
    print("=" * 60)
    print(f"{label} ...")
    return time.perf_counter()


def _done(t0: float, summary: str):
    elapsed = time.perf_counter() - t0
    print(f"  -> {summary}  ({elapsed:.1f}s)")


def run_pipeline(chip_part: str, skip_embed: bool = False) -> None:
    db_path = locate(chip_part)

    pages_jsonl = Path("data/parsed/pages_sm.jsonl")
    tables_jsonl = Path("data/parsed/tables_sm.jsonl")
    figures_jsonl = Path("data/parsed/figures_sm.jsonl")
    chunks_jsonl = Path("data/parsed/chunks.jsonl")
    chroma_dir = Path("data/store/chroma")

    pipeline_start = time.perf_counter()

    t0 = _step("Step 1: Parsing prose + general tables from freeWord")
    prose_count, table_count = parse_freewords(db_path, pages_jsonl, tables_jsonl)
    _done(t0, f"{prose_count} prose rows, {table_count} general tables")

    t0 = _step("Step 2: Building figure discovery index")
    figure_count = parse_figures(db_path, figures_jsonl)
    _done(t0, f"{figure_count} figures")

    t0 = _step("Step 3: Building chunks")
    counts = build_chunks(pages_jsonl, tables_jsonl, figures_jsonl, chip_part, chunks_jsonl)
    total = sum(counts.values())
    _done(t0, f"{total} chunks {counts}")

    if not skip_embed:
        t0 = _step("Step 4: Embedding + indexing into ChromaDB (local model, no API key)")
        n = build_index(chunks_jsonl, chroma_dir)
        _done(t0, f"{n} documents indexed")
    else:
        print("Step 4: SKIPPED (--skip-embed)")

    total_elapsed = time.perf_counter() - pipeline_start
    print("=" * 60)
    print(f"Ingestion complete.  Total time: {total_elapsed:.1f}s")


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chip_part = args[0] if args else "RA6M4"
    skip_embed = "--skip-embed" in sys.argv
    run_pipeline(chip_part, skip_embed=skip_embed)
