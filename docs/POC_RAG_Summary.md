# POC Summary — RAG over Hardware User Manuals (MCP Agent Interface)

## What It Is

A local RAG system that gives AI coding agents (Claude Desktop, GitHub Copilot, Cursor, or any MCP-compatible agent) **grounded, cited answers** about a semiconductor chip's Hardware User Manual — directly inside the developer's IDE or chat session.

No cloud hosting. No separate UI. The agent calls the tools; the developer stays in their workflow.

## The Problem It Solves

Embedded developers spend significant time hunting through hardware UMs for register addresses, bit-field meanings, and peripheral diagrams. Current options:

- **Manual PDF search** — slow, easy to miss related context across sections
- **Generic LLM chat** — hallucinates register details confidently and without warning
- **Copy-paste into Claude/Copilot** — no UM indexing, no citations, no refusal when unsure

This POC turns the UM into a **set of callable tools** that any AI agent can invoke mid-conversation, with verifiable, source-linked answers.

## Where the Data Comes From

The Renesas Smart Manual VS Code extension already downloads a structured SQLite database per chip to local disk. Rather than re-parsing the PDF, this POC reads that database directly:

- **Prose** (`freeWord.keyword`) → chunked and embedded into ChromaDB
- **Figures** — embedded as native `<svg>` inside the DB's HTML content → extracted as SVG files + figure chunks
- **Registers & bit-fields** (`registerList` / `bitList`) → queried live at request time, no import step

## How It Works — 30-Second Version

```
Smart Manual DB (SQLite) ──▶ freeWord + figures ──▶ chunk ──▶ embed ──▶ ChromaDB
                     │
                     └──▶ registerList / bitList — queried live, no copy
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
- **`register_lookup`** — deterministic exact lookup by register name, queried live
- **`get_figure`** — retrieve a figure by ID with its native SVG image and caption

## Key Design Choices

| Choice | Why |
|---|---|
| Smart Manual DB as the data source | Already structured and already on disk — no PDF re-parsing needed |
| Registers queried live, no local copy | The source data is already normalized SQLite |
| No fallback in the DB locator | Keeps the tool honest about what's actually available locally |
| MCP server as primary interface | Agent calls tools mid-conversation — no context-switching, no separate UI |
| Single Chroma collection (prose + figure) | One retrieval hop, simpler filtering |
| Citation baked into tool response | Every returned item carries a citation the agent can't lose |
| Figures kept as native SVG | Source figures are vector — rasterizing would lose fidelity |
| Local sentence-transformers embeddings (`all-MiniLM-L6-v2`) | Fully offline — no external API call at serve-time |
| Local-only for POC | No server, no auth, no cloud cost — runs on developer's machine |

## Stack

Python 3.11 · ChromaDB · sentence-transformers (`all-MiniLM-L6-v2`) · SQLite · BeautifulSoup · **FastMCP**

## MCP Tool Signatures

```python
search_um(query: str, chip_part: str, top_k: int = 6) -> list[Chunk] | dict
# Returns top-k chunks with section_title, render_text, citation string
# Returns {"refusal": "..."} when similarity < 0.30

register_lookup(name: str, chip_part: str) -> list[RegisterRecord]
# Returns register record(s) queried live from the Smart Manual DB: address, bit_fields, citation

get_figure(figure_id: str, chip_part: str) -> FigureRecord | None
# Returns figure caption, SVG image, section_title, citation
```

## Scope

| In scope | Out of scope |
|---|---|
| MCP tool server (local) | Cloud hosting / remote deployment |
| Prose Q&A via `search_um` | Code generation / FSP driver config |
| Register lookup via `register_lookup` | Multi-UM cross-synthesis |
| Figure recall via `get_figure` | Chips without a local Smart Manual DB |
| Claude Desktop + VS Code Copilot demo | GPU-accelerated embedding |

## Current State

- **Chip:** RA6M4, sourced from its Smart Manual DB (not the PDF)
- **Architecture:** finalized; implementation in progress
- **Previous baseline:** an earlier PDF-based pipeline reached 100% pass rate (69/69) on an earlier golden set — that set will be updated for the new register naming and citation format, then re-run against this approach

## Deliverables

- MCP server (`app/mcp_server.py`) runnable as a local process
- `.mcp.json` config for VS Code / RICA IDE integration
- Ingestion pipeline (`python -m ingest.run_all`) for prose + figures
- Golden set (`eval/golden_set_v2.csv`) + eval runner (`eval/run.py`)
- All tool responses carry a `citation` field

## Running the System

```bash
# 1. Ingest prose + figures (one-shot)
python -m ingest.run_all

# 2. Run eval
python -m eval.run

# 3. Start MCP server (used by IDE agent)
python -m app.mcp_server
```

---

*Full spec:* [POC_RAG_Spec.md](POC_RAG_Spec.md) · *Implementation tasks:* [POC_RAG_Tasks.md](POC_RAG_Tasks.md)
