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


def run_pipeline(
    skip_figures: bool = False,
    skip_embed: bool = False,
    only_doc_id: str | None = None,
) -> None:
    registry_path = Path("data/registry.json")
    registry = json.loads(registry_path.read_text())

    docs = [d for d in registry if d["doc_id"] == only_doc_id] if only_doc_id else registry
    if only_doc_id and not docs:
        print(f"ERROR: doc_id {only_doc_id!r} not found in {registry_path}")
        sys.exit(1)

    pages_jsonl = Path("data/parsed/pages.jsonl")
    figures_jsonl = Path("data/parsed/figures.jsonl")
    tables_jsonl = Path("data/parsed/tables.jsonl")

    pipeline_start = time.perf_counter()

    # Steps 1-3 run once per document, appending doc_id-tagged rows into the
    # shared intermediates. On a full multi-doc run, clear the files once up
    # front. On a single-doc re-ingest (only_doc_id), preserve other documents'
    # rows but drop this doc_id's own rows first so re-running doesn't duplicate.
    if not only_doc_id:
        pages_jsonl.parent.mkdir(parents=True, exist_ok=True)
        for p in (pages_jsonl, figures_jsonl, tables_jsonl):
            p.write_text("")
    else:
        pages_jsonl.parent.mkdir(parents=True, exist_ok=True)
        for p in (pages_jsonl, figures_jsonl, tables_jsonl):
            if not p.exists():
                p.write_text("")
                continue
            kept = [
                line for line in p.read_text(encoding="utf-8").splitlines(keepends=True)
                if line.strip() and json.loads(line).get("doc_id") != only_doc_id
            ]
            p.write_text("".join(kept), encoding="utf-8")

    t0 = _step(f"Steps 1-3: Parsing {len(docs)} document(s)")
    for doc_info in docs:
        pdf_path = Path(doc_info["path"])
        doc_id = doc_info["doc_id"]

        if not pdf_path.exists():
            print(f"  WARNING: PDF not found at {pdf_path} — skipping {doc_id}")
            continue

        print(f"  -- {doc_id} ({pdf_path}) --")
        mode = "a"
        n = parse_text(pdf_path, pages_jsonl, doc_id=doc_id, mode=mode)
        print(f"     Step 1: {n} text blocks")

        if not skip_figures:
            n = parse_figures(
                pdf_path=pdf_path,
                doc_id=doc_id,
                pages_jsonl=pages_jsonl,
                output_jsonl=figures_jsonl,
                figures_dir=Path("data/figures"),
                mode=mode,
            )
            print(f"     Step 2: {n} figures")
        elif not figures_jsonl.exists():
            figures_jsonl.write_text("")

        n = parse_tables(
            pdf_path,
            tables_jsonl,
            pages_jsonl=pages_jsonl,
            figures_jsonl=figures_jsonl,
            doc_id=doc_id,
            mode=mode,
        )
        print(f"     Step 3: {n} tables")
    _done(t0, "parsing complete")

    # Step 4: Build SQLite register database
    t0 = _step("Step 4: Building SQLite register database")
    n = build_register_db(
        tables_jsonl,
        pages_jsonl,
        registry_path,
        Path("data/store/registers.db"),
        only_doc_id=only_doc_id,
    )
    _done(t0, f"{n} registers")

    # Step 5: Build SQLite general_tables database (non-register tables)
    t0 = _step("Step 5: Building SQLite general_tables database")
    n = build_general_tables_db(
        tables_jsonl,
        registry_path,
        Path("data/store/registers.db"),
        only_doc_id=only_doc_id,
    )
    _done(t0, f"{n} general tables")

    # Step 6: Build chunks
    t0 = _step("Step 6: Building chunks")
    counts = build_chunks(
        pages_jsonl,
        figures_jsonl,
        tables_jsonl,
        Path("data/store/registers.db"),
        registry_path,
        Path("data/parsed/chunks.jsonl"),
        only_doc_id=only_doc_id,
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
