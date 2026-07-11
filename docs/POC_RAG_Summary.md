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
- **`get_figure`** — retrieve a figure by ID with image path and VLM caption

## Key Design Choices

| Choice | Why |
|---|---|
| MCP server as primary interface | Agent calls tools mid-conversation — no context-switching, no separate UI |
| SQLite for registers | Eliminates the #1 hallucination risk — wrong addresses / reset values |
| Single Chroma collection (prose + register_row + figure) | One retrieval hop, simpler filtering |
| Citation validator in tool response | Every chunk returned already carries `【DOC | § | p】` — the agent can't lose it |
| Similarity threshold (< 0.30 → refusal string) | Tool returns a refusal message rather than low-confidence chunks |
| VLM one-shot per figure, SHA-256 cache | Figures are searchable without a multimodal retriever |
| Local-only for POC | No server, no auth, no cloud cost — runs on developer's machine |

## Stack

Python 3.11 · LangChain · ChromaDB · PyMuPDF · pdfplumber · OpenAI (`gpt-4o-mini`, `text-embedding-3-small`) · SQLite · **MCP SDK (`mcp` / `fastmcp`)**

## MCP Tool Signatures

```python
search_um(query: str, chip_part: str, top_k: int = 6) -> list[Chunk]
# Returns top-k chunks with section_path, page, render_text, citation string

register_lookup(name: str, chip_part: str) -> list[RegisterRecord]
# Returns full register record(s): address, reset, bit_fields, section, page

get_figure(figure_id: str, chip_part: str) -> FigureRecord
# Returns figure caption, VLM summary, image_path, section_path, page
```

## Scope

| In scope | Out of scope |
|---|---|
| MCP tool server (local) | Cloud hosting / remote deployment |
| Prose Q&A via `search_um` | Code generation / FSP driver config |
| Register lookup via `register_lookup` | Multi-UM cross-synthesis |
| Figure recall via `get_figure` | ColPali-style multimodal retrieval |
| Claude Desktop + VS Code Copilot demo | Eval harness, scaling, GPU |

## Deliverables

- MCP server (`app/mcp_server.py`) runnable as a local process
- `claude_desktop_config.json` snippet and VS Code MCP extension config for demo setup
- Ingestion pipeline (one-shot, ~2 hrs on a laptop)
- 40-question golden set + smoke eval script (calls MCP tools directly)
- All tool responses carry `【DOC | § | p】` citations

## Success Criteria

- Agent (Claude Desktop or Copilot) can answer prose, register, and figure questions using only the MCP tools — no external knowledge needed
- ≥ 80% pass rate on 40-question golden set
- `register_lookup` returns verbatim register data — zero hallucinated addresses or bit values
- Tool returns a refusal string (never empty chunks) when similarity < 0.30
- VLM captioning cost < $5 for one full UM

## Demo Flow (on developer's machine)

1. Run ingestion once: `python -m ingest.run`
2. Start MCP server: `python -m app.mcp_server`
3. Open Claude Desktop (or Copilot Chat) — UM tools appear automatically
4. Ask: *"What does the SCKCR register control?"* → agent calls `register_lookup`, returns cited answer
5. Ask: *"Show me the clock generation block diagram"* → agent calls `get_figure`, returns caption + image path
6. Ask: *"How do I configure AGT in one-shot mode?"* → agent calls `search_um`, returns cited prose chunks

## Timeline

~2 weeks, one engineer, no GPU required, local machine only.

---

*Full spec:* [POC_RAG_Spec.md](POC_RAG_Spec.md) · *Implementation tasks:* [POC_RAG_Tasks.md](POC_RAG_Tasks.md)
