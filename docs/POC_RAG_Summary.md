# POC Summary — RAG over Hardware User Manuals (MCP Agent Interface)

## What It Is

A local RAG system that gives AI coding agents (Claude Desktop, GitHub Copilot, Cursor, or any MCP-compatible agent) **grounded, cited answers** about a semiconductor chip's Hardware User Manual — directly inside the developer's IDE or chat session.

No cloud hosting. No separate UI. The agent calls the tools; the developer stays in their workflow.

## The Problem It Solves

Embedded developers spend significant time hunting through 1500+ page Hardware UMs for register addresses, bit-field meanings, peripheral diagrams, and cross-section context. Current options:

- **Manual PDF search** — slow, easy to miss related context across sections
- **Generic LLM chat** — hallucinates register details confidently and without warning
- **Copy-paste into Claude/Copilot** — no UM indexing, no citations, no refusal when unsure

This POC turns the UM into a **set of callable tools** that any AI agent can invoke mid-conversation, with verifiable, source-linked answers.

## How It Works — 30-Second Version

```
PDF ──▶ parse (text + tables + figures) ──▶ embed ──▶ ChromaDB
                     │
                     └──▶ SQLite (register exact values)
                                    │
                          MCP Server (local process)
                          ┌─────────────────────────┐
                          │  tool: search_um         │
                          │  tool: register_lookup   │
                          │  tool: get_figure        │
                          └─────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             Claude Desktop    VS Code         Cursor / any
             (claude_desktop   Copilot Chat    MCP client
              _config.json)    (MCP ext.)
```

Three tools, one local server:
- **`search_um`** — semantic search over prose + figures, returns cited chunks
- **`register_lookup`** — deterministic exact lookup by register name from SQLite
- **`get_figure`** — retrieve a figure by ID with base64-encoded image and VLM caption

## Key Design Choices

| Choice | Why |
|---|---|
| MCP server as primary interface | Agent calls tools mid-conversation — no context-switching, no separate UI |
| SQLite for registers | Eliminates the #1 hallucination risk — wrong addresses / reset values |
| Single Chroma collection (prose + register_row + figure) | One retrieval hop, simpler filtering |
| Citation baked into tool response | Every chunk returned already carries `【DOC | § | p】` — the agent can't lose it |
| Similarity threshold (< 0.30 → refusal string) | Tool returns a refusal message rather than low-confidence chunks |
| Local sentence-transformers embeddings (`all-MiniLM-L6-v2`) | Fully offline — no OpenAI API call at serve-time |
| HTTPS figure server (port 7477, self-signed cert) | Serves figure PNGs to agent via URL; base64 fallback for MCP |
| Local-only for POC | No server, no auth, no cloud cost — runs on developer's machine |

## Stack

Python 3.11 · LangChain · ChromaDB · PyMuPDF · pdfplumber · sentence-transformers (`all-MiniLM-L6-v2`) · SQLite · **FastMCP**

LLM endpoint (internal Databricks proxy, only needed for test-set generation): `databricks-claude-sonnet-4-6`

## MCP Tool Signatures

```python
search_um(query: str, chip_part: str, top_k: int = 6) -> list[Chunk] | dict
# Returns top-k chunks with section_path, page, render_text, citation string
# Returns {"refusal": "..."} when similarity < 0.30

register_lookup(name: str, chip_part: str) -> list[RegisterRecord]
# Returns full register record(s): address, reset, bit_fields, section, page

get_figure(figure_id: str, chip_part: str) -> FigureRecord | None
# Returns figure caption, VLM summary, image as base64 data URI, section_path, page
```

## Scope

| In scope | Out of scope |
|---|---|
| MCP tool server (local) | Cloud hosting / remote deployment |
| Prose Q&A via `search_um` | Code generation / FSP driver config |
| Register lookup via `register_lookup` | Multi-UM cross-synthesis |
| Figure recall via `get_figure` | ColPali-style multimodal retrieval |
| Claude Desktop + VS Code Copilot demo | GPU-accelerated embedding |

## Current State

- **Document indexed:** RA6M4 User's Manual Rev.1.60 (`R01UH0890EJ0160`)
- **Registers in SQLite:** 511 registers · 3,303 bit fields
- **Eval:** 69-question golden set · **94% pass rate** (65/69)
  - `register_lookup`: 100% pass
  - `get_figure`: 100% pass
  - `search_um`: 4 failures (3× `wrong_page`, 1× `wrong_section`)

## Deliverables

- MCP server (`app/mcp_server.py`) runnable as a local process
- `.mcp.json` config for VS Code / RICA IDE integration
- Ingestion pipeline (`python -m ingest.run_all`, one-shot)
- 69-question golden set (`eval/golden_set_v2.csv`) + eval runner (`eval/run.py`)
- All tool responses carry `【DOC | § | p】` citations

## Running the System

```bash
# 1. Ingest (one-shot, ~10-20 min)
python -m ingest.run_all

# 2. Run eval
python -m eval.run

# 3. Start MCP server (used by IDE agent)
python -m app.mcp_server
```

---

*Full spec:* [POC_RAG_Spec.md](POC_RAG_Spec.md) · *Implementation tasks:* [POC_RAG_Tasks.md](POC_RAG_Tasks.md)
