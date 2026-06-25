# POC — RAG over Hardware User Manuals (MCP Agent Interface)

**Stack:** Python 3.11 · LangChain · ChromaDB · PyMuPDF · pdfplumber · OpenAI (`text-embedding-3-small`, `gpt-4o-mini`) · SQLite · FastMCP
**Scope:** 1–2 PDF UMs (start with `r01uh0890ej0150-ra6m4.pdf`), English only, read-only, local machine
**Timeline:** ~2 weeks, one engineer, no GPU

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
| `get_figure` | ChromaDB | Figure record: caption, VLM summary, image path |

### Success Criteria

- ≥ 80% pass on 40-question golden set (tools called directly, no agent in the loop)
- All returned chunks include a pre-formatted `【DOC | § | p】` citation
- `register_lookup` never returns hallucinated data — SQLite only
- Tool returns refusal string (not empty chunks) when similarity < 0.30
- VLM cost < $5 for one full UM

### Task Sequence

```
Task 0   Repo bootstrap
Task 1   Register the UM (registry.json)
Task 2   Parse text + TOC
Task 3   Detect register tables
Task 4   Build register schema + SQLite
Task 5   Extract figures + VLM captions
Task 6   Chunking (prose / register_row / figure)
Task 7   Embed + index in Chroma
Task 8   Register lookup tool
Task 9   Retriever + figure tool
Task 10  MCP server (search_um, register_lookup, get_figure)
Task 11  Golden set & smoke eval
Task 12  Second UM smoke test  ← stretch goal
```

See [POC_RAG_Tasks.md](POC_RAG_Tasks.md) for full actions and checkpoints.
