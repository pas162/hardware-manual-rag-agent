# Hardware Manual RAG Agent

A local RAG system that gives AI coding agents **grounded, cited answers** from any hardware User Manual (UM) PDF — exposed as MCP tools that work directly inside Claude Desktop, VS Code Copilot, Cursor, or any MCP-compatible client.

No cloud hosting. No separate UI. Drop in a PDF, run ingestion once, and your agent can query it mid-conversation.

---

## What It Does

Embedded developers spend significant time hunting through 1000+ page hardware UMs for register addresses, bit-field meanings, and peripheral diagrams. This project turns any UM PDF into callable MCP tools:

| Tool | What it does |
|---|---|
| `search_um` | Semantic search over prose, tables, and figures — returns cited chunks |
| `register_lookup` | Deterministic SQLite lookup by register name — returns address, reset value, all bit fields |
| `query_register_field` | Deterministic SQLite lookup of a single bit field by symbol or bit index — no full register payload |
| `get_figure` | Retrieve a figure by ID — returns caption, VLM summary, and image path |
| `get_table` | Deterministic SQLite lookup of a general (non-register) table by ID — returns the full markdown |

Every response carries a `【DOC | § | p.N】` citation. The retriever refuses (returns a refusal string) rather than returning low-confidence chunks.

---

## Architecture

```
PDF ──▶ parse (text + tables + figures) ──▶ embed ──▶ ChromaDB
                     │
                     └──▶ SQLite (registers + bit fields)
                                    │
                          MCP Server (SSE — http://localhost:8765/sse)
                          ┌─────────────────────────────┐
                          │  tool: search_um             │
                          │  tool: register_lookup       │
                          │  tool: query_register_field  │
                          │  tool: get_figure            │
                          │  tool: get_table             │
                          └─────────────────────────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    ▼               ▼               ▼
             Claude Desktop    VS Code Copilot   Cursor / any
                                (MCP extension)  MCP client
```

### Ingestion pipeline (one-shot, run once per PDF)

| Step | Module | Output |
|---|---|---|
| 1. Parse text + TOC | `ingest/parser_text.py` | `data/parsed/pages.jsonl` |
| 2. Extract figures | `ingest/parser_figures.py` | `data/parsed/figures.jsonl` + `data/figures/` |
| 3. Detect tables | `ingest/parser_tables.py` | `data/parsed/tables.jsonl` |
| 4. Build register DB | `ingest/register_schema.py` | `data/store/registers.db` |
| 5. Build chunks | `ingest/chunker.py` | `data/parsed/chunks.jsonl` |
| 6. Embed + index | `ingest/indexer.py` | `data/store/chroma/` |

### MCP runtime

| Module | Role |
|---|---|
| `app/mcp_server.py` | FastMCP server (SSE transport) — exposes the three tools |
| `app/retriever.py` | ChromaDB semantic search with similarity threshold guard |
| `app/register_tool.py` | SQLite register lookup (exact + prefix match) |
| `app/figure_tool.py` | Figure retrieval from ChromaDB by figure ID |

---

## Requirements

- Python 3.11+
- No GPU required — uses `BAAI/bge-large-en-v1.5` on CPU by default (configurable via `EMBED_MODEL` in `.env`; larger and slower than the previous MiniLM default, but higher retrieval quality)
- No OpenAI API key required for ingestion or retrieval

---

## Setup

### 1. Clone and install

```bash
git clone https://github.com/pas162/hardware-manual-rag-agent.git
cd hardware-manual-rag-agent
python -m venv .venv

# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

### 2. Add your PDF

Place your hardware UM PDF anywhere under `data/pdfs/`, then edit `data/registry.json`:

```json
[
  {
    "doc_id":    "YOUR_DOC_ID",
    "revision":  "1.00",
    "chip_part": "YOUR_CHIP",
    "path":      "data/pdfs/your-hardware-um.pdf"
  }
]
```

- `doc_id` — any short identifier used in citations (e.g. `R01UH0890EJ0160`)
- `chip_part` — the identifier agents pass to tool calls (e.g. `RA6M4`, `STM32H7`)
- `path` — path to the PDF relative to the project root

Multiple documents are supported — add more objects to the array.

### 3. Run ingestion

```bash
python -m ingest.run_all
```

Optional flags:
```bash
python -m ingest.run_all --skip-figures   # skip figure extraction (faster)
python -m ingest.run_all --skip-embed     # skip ChromaDB indexing (re-run parse only)
```

Ingestion takes ~30–90 minutes on a laptop depending on PDF size. It only needs to run once.

### 4. Configure the MCP server

Copy `.mcp.json.example` to `.mcp.json` and update the paths:

```json
{
  "mcpServers": {
    "hardware-um": {
      "url": "http://localhost:8765/sse"
    }
  }
}
```

The server must be running before your agent connects (see step 3).
For stdio-only clients (RICA, older Copilot), use the Node stdio-to-SSE bridge
described in `.mcp.json.example`.

### 5. Connect your agent

**Claude Desktop** — add the server block to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "hardware-um": { ...same block as above... }
  }
}
```

**VS Code Copilot** — install the MCP extension, point it at `.mcp.json` in the workspace root.

**Cursor / other MCP clients** — use the same server block in their respective config files.

### 6. Verify the server

```bash
npx @modelcontextprotocol/inspector --url http://localhost:8765/sse
```

---

## Usage Examples

Once connected, ask your agent naturally:

```
"What does the SCKCR register control?"
→ agent calls register_lookup("SCKCR", "YOUR_CHIP")
→ returns address, reset value, all bit fields, citation

"How do I configure the AGT timer in one-shot mode?"
→ agent calls search_um("AGT one-shot mode configuration", "YOUR_CHIP")
→ returns cited prose chunks from the relevant section

"Show me the clock generation block diagram."
→ agent calls get_figure("Figure 8.1", "YOUR_CHIP")
→ returns caption, image path, section and page citation
```

---

## Tool Reference

### `search_um(query, chip_part, top_k=6)`

Semantic search over all indexed content (prose, register rows, figures, tables).

```python
# Returns list[dict] or {"refusal": "..."} when similarity < threshold
{
  "element_type": "prose" | "register_row" | "figure" | "table",
  "section_path": "§8 > §8.2 Clock Generation Circuit",
  "page": 102,
  "render_text": "...",
  "peripheral": "",
  "register_name": "",
  "figure_id": "",
  "image_path": "",
  "citation": "【R01UH0890EJ0160 Rev.1.60 | §8 > §8.2 | p.102】",
  "score": 0.1823
}
```

### `register_lookup(name, chip_part)`

Exact + prefix match lookup. Handles indexed register names (e.g. `IELSRn` matches `IELSR0`, `IELSR1`, …).

```python
# Returns list[dict] — one entry per peripheral match
{
  "peripheral": "ICU",
  "register_name": "IELSRn",
  "address": "0x40006300 + 4*n",
  "size_bits": 32,
  "reset_value": "0x00000000",
  "access": "R/W",
  "section_path": "§13 > §13.3.2",
  "page": 284,
  "bit_fields": [
    {"bits": "31", "symbol": "IR", "access": "R/W", "reset": "0", "description": "..."},
    ...
  ],
  "citation": "【R01UH0890EJ0160 Rev.1.60 | §13 > §13.3.2 | p.284】"
}
```

### `query_register_field(register_name, bit_or_symbol, chip_part)`

Precise single-field lookup — no full register payload. Matches by symbol name (e.g. `"IR"`) or bit index (e.g. `"16"`).

```python
# Returns dict or null
{
  "peripheral": "ICU",
  "register_name": "IELSRn",
  "address": "0x40006300 + 4*n",
  "bits": "16",
  "symbol": "IR",
  "access": "R/W",
  "reset": "0",
  "description": "...",
  "section_path": "§13 > §13.3.2",
  "page": 284,
  "citation": "【R01UH0890EJ0160 Rev.1.60 | §13 > §13.3.2 | p.284】"
}
```

### `get_figure(figure_id, chip_part)`

Retrieves a figure by its label (e.g. `"Figure 13.2"`).

```python
# Returns dict or null
{
  "figure_id": "Figure 13.2",
  "caption": "ICU Block Diagram",
  "vlm_summary": "",          // reserved — no VLM used
  "image_path": "data/figures/R01UH0890EJ0160/p281_fig_13_2.png",
  "section_path": "§13 > §13.1",
  "page": 281,
  "citation": "【R01UH0890EJ0160 Rev.1.60 | §13 > §13.1 | p.281 | Figure 13.2】"
}
```

---

## Eval

A 40-question golden set is included for smoke-testing after ingestion. Update `CHIP_PART` in `eval/run.py` to match your `chip_part`, then:

```bash
python -m eval.run
```

Outputs a pass/fail table to `eval/results.md`. Target: ≥ 80% pass rate.

---

## Design Notes

| Decision | Rationale |
|---|---|
| SQLite for registers | Eliminates the #1 hallucination risk — wrong addresses / reset values |
| Similarity threshold guard | Returns a refusal string rather than low-confidence chunks |
| Single Chroma collection | One retrieval hop; filter by `chip_part` for multi-doc support |
| Figure zones in table parser | Prevents pdfplumber from detecting drawing lines inside figures as tables |
| Local embedding model (bge-large, configurable) | No API key, no cost, runs on CPU |
| SSE MCP transport   | Single long-running server; stdio clients use a thin bridge |

---

## Stack

Python 3.11 · FastMCP · LangChain · ChromaDB · PyMuPDF · pdfplumber · SQLite · `BAAI/bge-large-en-v1.5`

For the full architecture and roadmap see [docs/PROJECT_PLAN.md](docs/PROJECT_PLAN.md).

---

## License

MIT
