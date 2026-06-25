# POC Spec — RAG over Hardware User Manuals (MCP Agent Interface)

*Part of: [POC_RAG_Hardware_UM_Plan.md](POC_RAG_Hardware_UM_Plan.md)*

---

## 1. Prerequisites

**Python:** 3.11

**Dependencies (`requirements.txt`):**
```
pymupdf
pdfplumber
chromadb
langchain
langchain-openai
langchain-community
openai
pillow
sqlite-utils
rank_bm25
mcp
fastmcp
pydantic
python-dotenv
```

> `streamlit` removed — the MCP server is the sole runtime interface.

**Environment (`.env`):**
```
OPENAI_API_KEY=<your key>
```

**Models:**
| Purpose | Model |
|---|---|
| Embeddings | `text-embedding-3-small` |
| Figure captioning | `gpt-4o-mini` (vision, one call per image) |

> No LLM wrapper needed at serve-time — the calling agent (Claude, Copilot, etc.) provides the LLM. The MCP server returns raw retrieved data; the agent reasons over it.

**Starting PDF:** `r01uh0890ej0150-ra6m4.pdf` (Renesas RA6M4 Hardware User Manual)

---

## 2. Architecture

### 2.1 Ingestion (offline, one-shot)

```
PDF UM
  │
  ├─▶ PyMuPDF ──────────────────────────────────────────────────────────────┐
  │     text blocks, TOC, section_path resolver, page.get_images()          │
  │                                                                          │
  ├─▶ pdfplumber ────────────────────────────────────────────────────────────┤
  │     table extraction, register-table heuristic                          │
  │                                                                          │
  │         ┌─────────────────────┬──────────────────────┬──────────────────┘
  │         ▼                     ▼                      ▼
  │    prose chunks          register tables         figure images
  │    (500 tok, 80 overlap)  → JSON schema          + nearest caption
  │                               │                       │
  │                               ▼                       ▼
  │                      SQLite registers.db        VLM one-shot caption
  │                               │                       │
  │                               └──────────┬────────────┘
  │                                          ▼
  │                              OpenAI text-embedding-3-small
  │                                          ▼
  └──────────────────────────────▶ ChromaDB persistent collection
```

### 2.2 Runtime — MCP Server + Agent

```
Developer's IDE / Chat client
  │
  │  (MCP protocol over stdio or SSE)
  ▼
app/mcp_server.py  ← local process on developer's machine
  │
  ├─▶ tool: search_um(query, chip_part, top_k=6)
  │     └─▶ Chroma similarity search + metadata filter
  │           └─▶ returns: list of Chunk {section_path, page, render_text, citation}
  │
  ├─▶ tool: register_lookup(name, chip_part)
  │     └─▶ SQLite exact lookup
  │           └─▶ returns: list of RegisterRecord {address, reset, bit_fields, citation}
  │
  └─▶ tool: get_figure(figure_id, chip_part)
        └─▶ Chroma filter by figure_id + metadata
              └─▶ returns: FigureRecord {caption, vlm_summary, image_path, citation}

Agent receives tool results → reasons → answers developer's question
```

**Key design decisions:**

| Decision | Rationale |
|---|---|
| MCP server as sole interface | Agent calls tools mid-conversation — no UI context-switching |
| No LLM in the server | The calling agent provides reasoning; the server provides only retrieved facts |
| SQLite for registers | Deterministic lookup eliminates hallucinated addresses / reset values |
| Single Chroma collection (all 3 types) | One retrieval hop, simpler metadata filtering |
| Citations baked into tool responses | Every returned chunk already carries `【DOC | § | p】` — the agent can't lose them |
| Similarity threshold enforced at tool level | Returns a refusal string rather than low-confidence chunks |

### 2.3 Agent Configuration (local demo)

**Claude Desktop** — add to `claude_desktop_config.json`:
```json
{
  "mcpServers": {
    "hardware-um": {
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "cwd": "<absolute path to project root>"
    }
  }
}
```

**VS Code Copilot Chat** — add to `.vscode/mcp.json`:
```json
{
  "servers": {
    "hardware-um": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "app.mcp_server"]
    }
  }
}
```

**Cursor / other MCP clients** — equivalent stdio transport config.

---

## 3. MCP Tool Contracts

### `search_um`

```python
search_um(query: str, chip_part: str, top_k: int = 6) -> list[Chunk]
```

| Field | Type | Notes |
|---|---|---|
| `query` | str | Natural-language question or keyword |
| `chip_part` | str | e.g. `"RA6M4"` — filters the Chroma collection |
| `top_k` | int | Default 6, max 10 |

Returns list of `Chunk`:
```json
{
  "element_type": "prose | register_row | figure",
  "section_path": "§13 > §13.2 > §13.2.4",
  "page": 283,
  "render_text": "[§13.2.4] The IELS bits select the interrupt source ...",
  "citation": "【R01UH0890EJ0150 Rev.1.50 | §13.2.4 | p.283】"
}
```

Returns `{"refusal": "No relevant content found in RA6M4 UM Rev.1.50."}` when top similarity score < 0.30.

---

### `register_lookup`

```python
register_lookup(name: str, chip_part: str) -> list[RegisterRecord]
```

Returns list (multiple records when the name is shared across peripherals):
```json
{
  "peripheral": "ICU",
  "register_name": "IELSRn",
  "address": "0x40006300 + 4×n",
  "size_bits": 32,
  "reset_value": "0x00000000",
  "access": "R/W",
  "section_path": "§13.2.4",
  "page": 283,
  "bit_fields": [
    {"bits": "31:9", "symbol": "—",    "access": "R",   "reset": "0", "description": "Reserved"},
    {"bits": "8",    "symbol": "IR",   "access": "R/W", "reset": "0", "description": "Interrupt status flag"},
    {"bits": "7:0",  "symbol": "IELS", "access": "R/W", "reset": "0", "description": "Interrupt event link select"}
  ],
  "citation": "【R01UH0890EJ0150 Rev.1.50 | §13.2.4 | p.283】"
}
```

Returns `[]` for unknown names.

---

### `get_figure`

```python
get_figure(figure_id: str, chip_part: str) -> FigureRecord | None
```

```json
{
  "figure_id": "Figure 13.2",
  "caption": "ICU Block Diagram",
  "vlm_summary": "Block diagram showing the ICU peripheral ...",
  "image_path": "data/figures/R01UH0890EJ0150/p280_42.png",
  "section_path": "§13.1",
  "page": 280,
  "citation": "【R01UH0890EJ0150 Rev.1.50 | §13.1 | p.280 | Figure 13.2】"
}
```

Returns `null` for unknown figure IDs.

---

## 4. Data Model

### 4.1 Chunk Metadata Envelope (10 fields)

| Field | Type | Description |
|---|---|---|
| `doc_id` | str | e.g. `R01UH0890EJ0150` |
| `revision` | str | e.g. `1.50` |
| `chip_part` | str | e.g. `RA6M4` |
| `section_path` | str | Resolved from TOC, e.g. `§13 > §13.2 > §13.2.4` |
| `page_start` | int | First page of the chunk |
| `page_end` | int | Last page of the chunk |
| `element_type` | str | `prose` \| `register_row` \| `figure` |
| `peripheral` | str | e.g. `AGT`, `SCI`, `PORT` |
| `register_name` | str | e.g. `IELSRn`, `SCKCR` |
| `figure_id` | str | e.g. `Figure 13.2` |
| `image_path` | str | Relative path to extracted PNG |

### 4.2 SQLite Schema (`registers.db`)

**`registers` table:**
```sql
CREATE TABLE registers (
    peripheral    TEXT,
    register_name TEXT,
    address       TEXT,
    size_bits     INTEGER,
    reset_value   TEXT,
    access        TEXT,
    doc_id        TEXT,
    revision      TEXT,
    section_path  TEXT,
    page_start    INTEGER,
    page_end      INTEGER,
    json          TEXT,
    PRIMARY KEY (peripheral, register_name)
);
```

**`bit_fields` table:**
```sql
CREATE TABLE bit_fields (
    peripheral    TEXT,
    register_name TEXT,
    bits          TEXT,
    symbol        TEXT,
    access        TEXT,
    reset         TEXT,
    description   TEXT,
    FOREIGN KEY (peripheral, register_name) REFERENCES registers
);
```

### 4.3 Document Registry (`data/registry.json`)

```json
[
  {
    "doc_id":    "R01UH0890EJ0150",
    "revision":  "1.50",
    "chip_part": "RA6M4",
    "path":      "data/pdfs/r01uh0890ej0150-ra6m4.pdf"
  }
]
```

---

## 5. Module Map

| Module | File | Responsibility |
|---|---|---|
| `parser_text` | `ingest/parser_text.py` | PyMuPDF text + TOC + `section_path` resolver |
| `parser_tables` | `ingest/parser_tables.py` | pdfplumber tables + register-table heuristic |
| `parser_figures` | `ingest/parser_figures.py` | Image extract + caption pairing + VLM description |
| `register_schema` | `ingest/register_schema.py` | Build JSON records + populate `registers.db` |
| `chunker` | `ingest/chunker.py` | Emit `prose`, `register_row`, `figure` chunks |
| `indexer` | `ingest/indexer.py` | Embed + persist into Chroma |
| `retriever` | `app/retriever.py` | Chroma retriever (top-k + metadata filter) + similarity threshold guard |
| `register_tool` | `app/register_tool.py` | `register_lookup(name, chip_part) -> list[dict]` via SQLite |
| `figure_tool` | `app/figure_tool.py` | `get_figure(figure_id, chip_part) -> dict` via Chroma filter |
| `mcp_server` | `app/mcp_server.py` | FastMCP server — exposes `search_um`, `register_lookup`, `get_figure` |

**Folder layout:**
```
project/
├── ingest/
├── app/
├── eval/
└── data/
    ├── pdfs/
    ├── figures/
    ├── parsed/
    └── store/
        ├── chroma/
        └── registers.db
```

---

## 6. Guardrails

### 6.1 Tool-Level Rules (enforced in MCP server, not by the agent)

| Rule | Enforcement point | Detail |
|---|---|---|
| Similarity threshold | `retriever.py` | Top score < 0.30 → return refusal string, not chunks |
| Top-k cap | `search_um` tool | Hard max k = 6 (10 with explicit override) |
| Real figures only | `get_figure` tool | Returns `null` for any `figure_id` not in the indexed set |
| Deterministic registers | `register_lookup` tool | Returns SQLite record verbatim — no LLM interpretation |
| Citation baked in | All tools | Every returned item includes a pre-formatted `citation` field |
| Scope guard | `search_um` tool | Returns refusal string for queries with no matches in the specified `chip_part` |

### 6.2 Suggested Agent System Prompt

When configuring the agent (e.g. as a Claude Project instruction or Copilot custom instructions), recommend:

```
You have access to hardware UM tools for the {chip_part} chip.

When answering questions about registers, peripherals, or figures:
1. Always call the appropriate tool first — do not answer from memory.
2. Quote register addresses, reset values, and bit positions verbatim from register_lookup results.
3. Cite every factual statement using the citation field returned by the tool:
   【{doc_id} Rev.{revision} | §{section} | p.{page}】
4. If a tool returns a refusal or null, tell the user the information is not available in the UM.
5. Do not generate driver code or register configuration sequences.
```

---

## 7. Eval Criteria

| Metric | Target |
|---|---|
| Golden set pass rate | ≥ 80% across all 40 questions |
| Citation coverage | 100% of factual sentences carry a valid `【…】` citation |
| Register accuracy | `register_lookup` returns verbatim data; agent quotes it without modification |
| Refusal on empty retrieval | 100% — tool never returns low-confidence chunks |
| Figure match rate | ≥ 90% of extracted images paired with a `figure_id` |
| VLM cost (one UM) | < $5 total |

**Golden set distribution:** 10 prose · 10 register · 10 figure · 10 cross-section

**Eval method:** `eval/run.py` calls the MCP tools directly (bypassing the agent), scores tool output against expected values.

**Failure categories** (reported in `eval/results.md`):

| Code | Meaning |
|---|---|
| `wrong_section` | Returned chunk's section does not match expected |
| `wrong_page` | Page outside expected range |
| `hallucinated_register` | Register tool returned invented data (should not happen — indicates SQLite issue) |
| `missing_citation` | Returned chunk has no `citation` field |
| `false_refusal` | Tool returned refusal for a query that had sufficient matching content |
