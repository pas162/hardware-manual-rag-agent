"""
Full ingestion pipeline runner.

Run from project root:
  python -m ingest.run_all                  # full pipeline
  python -m ingest.run_all --skip-figures   # skip figure extraction
  python -m ingest.run_all --skip-embed     # skip ChromaDB indexing

Steps:
  1. parser_text    -> data/parsed/pages.jsonl
  2. parser_figures -> data/parsed/figures.jsonl  (must run before tables)
  3. parser_tables  -> data/parsed/tables.jsonl   (uses figures to exclude figure zones)
  4. register_schema -> data/store/registers.db
  5. table_schema    -> data/store/registers.db (general_tables table)
  6. chunker        -> data/parsed/chunks.jsonl
  7. indexer        -> data/store/chroma/
"""

import json
import sys
import time
from pathlib import Path

from ingest.parser_text import parse_text
from ingest.parser_figures import parse_figures
from ingest.parser_tables import parse_tables
from ingest.register_schema import build_register_db
from ingest.table_schema import build_general_tables_db
from ingest.chunker import build_chunks
from ingest.indexer import build_index


def _step(label: str):
    print("=" * 60)
    print(f"{label} ...")
    return time.perf_counter()


def _done(t0: float, summary: str):
    elapsed = time.perf_counter() - t0
    print(f"  -> {summary}  ({elapsed:.1f}s)")


def run_pipeline(skip_figures: bool = False, skip_embed: bool = False) -> None:
    registry_path = Path("data/registry.json")
    registry = json.loads(registry_path.read_text())
    doc_info = registry[0]
    pdf_path = Path(doc_info["path"])
    doc_id = doc_info["doc_id"]

    if not pdf_path.exists():
        print(f"ERROR: PDF not found at {pdf_path}")
        print(f"Place the UM PDF at {pdf_path} and re-run.")
        sys.exit(1)

    pipeline_start = time.perf_counter()

    # Step 1: Parse text + TOC
    t0 = _step("Step 1: Parsing text + TOC")
    n = parse_text(pdf_path, Path("data/parsed/pages.jsonl"))
    _done(t0, f"{n} text blocks")

    # Step 2: Extract figures — must run before table detection so figure zones
    # can be used to exclude false-positive tables inside figures.
    if not skip_figures:
        t0 = _step("Step 2: Extracting figures (no VLM — caption matching only)")
        n = parse_figures(
            pdf_path=pdf_path,
            doc_id=doc_id,
            pages_jsonl=Path("data/parsed/pages.jsonl"),
            output_jsonl=Path("data/parsed/figures.jsonl"),
            figures_dir=Path("data/figures"),
        )
        _done(t0, f"{n} figures")
    else:
        print("Step 2: SKIPPED (--skip-figures)")
        fig_path = Path("data/parsed/figures.jsonl")
        if not fig_path.exists():
            fig_path.write_text("")

    # Step 3: Detect tables (uses figures.jsonl to skip figure-zone detections)
    t0 = _step("Step 3: Detecting tables")
    n = parse_tables(
        pdf_path,
        Path("data/parsed/tables.jsonl"),
        pages_jsonl=Path("data/parsed/pages.jsonl"),
        figures_jsonl=Path("data/parsed/figures.jsonl"),
    )
    _done(t0, f"{n} tables")

    # Step 4: Build SQLite register database
    t0 = _step("Step 4: Building SQLite register database")
    n = build_register_db(
        Path("data/parsed/tables.jsonl"),
        Path("data/parsed/pages.jsonl"),
        registry_path,
        Path("data/store/registers.db"),
    )
    _done(t0, f"{n} registers")

    # Step 5: Build SQLite general_tables database (non-register tables)
    t0 = _step("Step 5: Building SQLite general_tables database")
    n = build_general_tables_db(
        Path("data/parsed/tables.jsonl"),
        registry_path,
        Path("data/store/registers.db"),
    )
    _done(t0, f"{n} general tables")

    # Step 6: Build chunks
    t0 = _step("Step 6: Building chunks")
    counts = build_chunks(
        Path("data/parsed/pages.jsonl"),
        Path("data/parsed/figures.jsonl"),
        Path("data/parsed/tables.jsonl"),
        Path("data/store/registers.db"),
        registry_path,
        Path("data/parsed/chunks.jsonl"),
    )
    total = sum(counts.values())
    _done(t0, f"{total} chunks {counts}")

    # Step 7: Embed + index into ChromaDB
    if not skip_embed:
        t0 = _step("Step 7: Embedding + indexing into ChromaDB (local model, no API key)")
        n = build_index(Path("data/parsed/chunks.jsonl"), Path("data/store/chroma"))
        _done(t0, f"{n} documents indexed")
    else:
        print("Step 7: SKIPPED (--skip-embed)")

    total_elapsed = time.perf_counter() - pipeline_start
    print("=" * 60)
    print(f"Ingestion complete.  Total time: {total_elapsed:.1f}s")


if __name__ == "__main__":
    skip_figures = "--skip-figures" in sys.argv
    skip_embed = "--skip-embed" in sys.argv
    run_pipeline(skip_figures=skip_figures, skip_embed=skip_embed)
