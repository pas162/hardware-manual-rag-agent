# POC — RAG over Hardware User Manuals (MCP Agent Interface)

**Stack:** Python 3.11 · ChromaDB · sentence-transformers (`all-MiniLM-L6-v2`) · SQLite · BeautifulSoup · FastMCP
**Scope:** RA6M4, English only, read-only, local machine
**Data source:** Smart Manual DB — the per-chip SQLite database the Renesas Smart Manual VS Code extension keeps on disk. Registers, bit-fields, prose, and figures are all read from it directly; no PDF parsing.
**Status:** New architecture defined — implementation in progress

---

## Documents

| Document | Purpose | Audience |
|---|---|---|
| [POC_RAG_Summary.md](POC_RAG_Summary.md) | What, why, and how in one page | Stakeholders, new team members |
| [POC_RAG_Spec.md](POC_RAG_Spec.md) | Architecture, tool contracts, data model, module map, guardrails | Engineers building or reviewing the system |
| [POC_RAG_Tasks.md](POC_RAG_Tasks.md) | Step-by-step implementation tasks with checkpoints | Agent or engineer executing the build |
| [SmartManual_DB_Analysis.md](SmartManual_DB_Analysis.md) | Raw schema/content analysis of the Smart Manual DB | Reference for anyone touching the DB layer |

---

## Quick Reference

### What It Builds

A local MCP server that exposes three tools — `search_um`, `register_lookup`, `get_figure` — so any MCP-compatible AI agent (Claude Desktop, GitHub Copilot, Cursor) can answer an embedded developer's questions about a chip's Hardware User Manual **without leaving their IDE**.

### Where the Data Comes From

The Smart Manual VS Code extension already downloads a structured SQLite database per chip to local disk. This POC reads that database directly instead of re-parsing the PDF:

| Data | Smart Manual table | How it's used |
|---|---|---|
| Prose | `freeWord.display_data` HTML, register `<table>`s and `<figure>`s stripped out | Chunked + embedded into ChromaDB |
| General/lookup tables | `freeWord.display_data` HTML, non-register `<table>`s (e.g. Function Comparison, Pin Lists) | Chunked + embedded into ChromaDB |
| Figures | `<figure>` blocks inside `display_data` HTML | Caption indexed for discovery; SVG read live on request, no files on disk |
| Registers & bit-fields | `registerList` / `bitList` | Queried live at request time — no import step |

> `freeWord.keyword` is not used directly — it flattens register bit-tables and figure/SVG label text into the prose with no separators, so ingestion parses `freeWord.display_data` (HTML) and classifies each `<table>` before deciding what to keep. See [POC_RAG_Tasks.md](POC_RAG_Tasks.md) Task 3.

### Three Tools

| Tool | Source | Returns |
|---|---|---|
| `search_um` | ChromaDB | Top-k cited chunks (prose, tables, figures) |
| `register_lookup` | Smart Manual DB (live query) | Register record: address, reset value, bit fields |
| `get_figure` | ChromaDB + disk | Figure record: caption, SVG image, section title |

### Running the System

```bash
# Ingest prose + figures (one-shot)
python -m ingest.run_all

# Run eval
python -m eval.run

# Start MCP server (used by IDE agent)
python -m app.mcp_server
```

### Task Sequence

```
Task 0   Repo bootstrap                                ✅
Task 1   Locate the Smart Manual DB (locator)           ⬜
Task 2   Register lookup — live query + HTML parsing    ⬜
Task 3   Prose ingestion (freeWord → chunks)             ⬜
Task 4   Figure ingestion (<svg> → files + chunks)       ⬜
Task 5   Embed + index in Chroma                         ⬜
Task 6   MCP server (tool contracts unchanged)           ⬜
Task 7   Update golden set + re-run eval                 ⬜
```

See [POC_RAG_Tasks.md](POC_RAG_Tasks.md) for full actions and checkpoints.
