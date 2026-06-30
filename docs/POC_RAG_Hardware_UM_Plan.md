# POC — RAG over Hardware User Manuals (MCP Agent Interface)

**Stack:** Python 3.11 · LangChain · ChromaDB · PyMuPDF · pdfplumber · sentence-transformers (`all-MiniLM-L6-v2`) · SQLite · FastMCP
**Scope:** 1–2 PDF UMs (RA6M4 Rev.1.60 complete), English only, read-only, local machine
**Status:** Complete — 100% eval pass rate

---

## Documents

| Document | Purpose | Audience |
|---|---|---|
| [POC_RAG_Summary.md](POC_RAG_Summary.md) | What, why, and how in one page | Stakeholders, new team members |
| [POC_RAG_Spec.md](POC_RAG_Spec.md) | Architecture, tool contracts, data model, module map, guardrails, eval criteria | Engineers building or reviewing the system |
| [POC_RAG_Tasks.md](POC_RAG_Tasks.md) | Step-by-step implementation tasks with checkpoints | Agent or engineer executing the build |

---

## Quick Reference

### What It Builds

A local MCP server that exposes three tools — `search_um`, `register_lookup`, `get_figure` — so any MCP-compatible AI agent (Claude Desktop, GitHub Copilot, Cursor) can answer an embedded developer's questions about a semiconductor Hardware User Manual **without leaving their IDE**.

### Three Tools

| Tool | Storage | Returns |
|---|---|---|
| `search_um` | ChromaDB | Top-k cited chunks (prose, register rows, figures) |
| `register_lookup` | SQLite | Exact register record: address, reset value, bit fields |
| `get_figure` | ChromaDB + disk | Figure record: caption, base64 image, section path, page |

### Current Eval Results

| Tool | Pass rate |
|---|---|
| `register_lookup` | 100% (24/24) |
| `get_figure` | 100% (15/15) |
| `search_um` | 100% (30/30) |
| **Overall** | **100% (69/69)** |

### Running the System

```bash
# Ingest (one-shot, ~10-20 min on first run)
python -m ingest.run_all

# Run eval
python -m eval.run

# Start MCP server (used by IDE agent)
python -m app.mcp_server
```

### Task Sequence

```
Task 0   Repo bootstrap                         ✅
Task 1   Register the UM (registry.json)        ✅
Task 2   Parse text + TOC                       ✅
Task 3   Detect register tables                 ✅
Task 4   Build register schema + SQLite         ✅  511 registers · 3,303 bit fields
Task 5   Extract figures                        ✅
Task 6   Chunking (prose/register_row/figure)   ✅
Task 7   Embed + index in Chroma                ✅
Task 8   Register lookup tool                   ✅
Task 9   Retriever + figure tool                ✅
Task 10  MCP server                             ✅
Task 11  Golden set & smoke eval                ✅  100% pass rate (69/69)
Task 12  Second UM smoke test                   ⬜ stretch goal
```

See [POC_RAG_Tasks.md](POC_RAG_Tasks.md) for full actions and checkpoints.
