# POC Spec — RAG over Hardware User Manuals (MCP Agent Interface)

*Part of: [POC_RAG_Hardware_UM_Plan.md](POC_RAG_Hardware_UM_Plan.md)*

---

## 1. Prerequisites

**Python:** 3.11+

**Dependencies (`requirements.txt`):**
```
pymupdf
pdfplumber
chromadb
langchain
langchain-community
langchain-chroma
langchain-text-splitters
langchain-huggingface
langchain-openai
sentence-transformers
pillow
sqlite-utils
rank_bm25
mcp
fastmcp
pydantic
python-dotenv
openai
ragas>=0.2
rapidfuzz
```

**Environment (`.env`):**
```
OPENAI_API_BASE=http://<proxy-host>:<port>/api   # internal Databricks proxy
OPENAI_API_KEY=<your key>
OPENAI_MODEL=databricks-claude-sonnet-4-6        # used only for test-set generation
JUDGE_MODEL=databricks-gpt-5-4                   # optional, for future LLM-judge eval
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
HF_HUB_OFFLINE=1                                 # fully offline at serve-time
```

**Models:**
| Purpose | Model |
|---|---|
| Embeddings (ingest + serve) | `sentence-transformers/all-MiniLM-L6-v2` (local, offline) |
| Test-set question generation | `databricks-claude-sonnet-4-6` (via internal proxy, ingest-only) |

> No LLM call at serve-time — the calling agent provides reasoning. The MCP server returns raw retrieved data only.

**Starting PDF:** `r01uh0890ej0160-ra6m4.pdf` (Renesas RA6M4 User's Manual Rev.1.60)

---

## 2. Architecture

### 2.1 Ingestion (offline, one-shot)

```
PDF UM
  │
  ├─▶ PyMuPDF ──────────────────────────────────────────────────────────────┐
  │     text blocks, TOC, section_path resolver                             │
  │                                                                          │
  ├─▶ pdfplumber ────────────────────────────────────────────────────────────┤
  │     table extraction, register-table heuristic (≥4 columns required)   │
  │                                                                          │
  │         ┌─────────────────────┬──────────────────────┬──────────────────┘
  │         ▼                     ▼                      ▼
  │    prose chunks          register tables         figure images
  │    (500 chars, 80 overlap) → JSON records          + nearest caption
  │                               │                       │
  │                               ▼                       ▼
  │                      SQLite registers.db       PNG saved to disk
  │                               │                (data/figures/{doc_id}/)
  │                               └──────────┬────────────┘
  │                                          ▼
  │                   sentence-transformers/all-MiniLM-L6-v2 (local)
  │                                          ▼
  └──────────────────────────────▶ ChromaDB persistent collection
```

Run ingestion: `python -m ingest.run_all`

### 2.2 Runtime — MCP Server + Agent

```
Developer's IDE / Chat client
  │
  │  (MCP protocol over stdio)
  ▼
app/mcp_server.py  ← local process on developer's machine
  │
  ├─▶ tool: search_um(query, chip_part, top_k=6)
  │     └─▶ Chroma similarity search + metadata filter
  │           └─▶ returns: list of Chunk {section_path, page, render_text, citation}
  │
  ├─▶ tool: register_lookup(name, chip_part)
  │     └─▶ SQLite exact + prefix lookup
  │           └─▶ returns: list of RegisterRecord {address, reset, bit_fields, citation}
  │
  └─▶ tool: get_figure(figure_id, chip_part)
        └─▶ Chroma filter by figure_id + metadata
              └─▶ returns: FigureRecord {caption, image as base64 data URI, section_path, page, citation}

Agent receives tool results → reasons → answers developer's question
```

**Key design decisions:**

| Decision | Rationale |
|---|---|
| MCP server as sole interface | Agent calls tools mid-conversation — no UI context-switching |
| No LLM in the server | The calling agent provides reasoning; the server provides only retrieved facts |
| SQLite for registers | Deterministic lookup eliminates hallucinated addresses / reset values |
| Local sentence-transformers embeddings | Fully offline at serve-time — no external API dependency |
| Single Chroma collection (all 3 types) | One retrieval hop, simpler metadata filtering |
| Citations baked into tool responses | Every returned chunk already carries `【DOC | § | p】` — the agent can't lose them |
| Similarity threshold enforced at tool level | Returns a refusal dict rather than low-confidence chunks |
| Hybrid BM25 + dense retrieval (RRF) | BM25 exact-keyword ranking merged with dense semantic ranking — prevents dense-embedding collisions (e.g. SSISCR vs SSICR) |
| Front-matter / TOC chunks excluded from index | §Contents, §Preface etc. indexed as noise before fix — now filtered at embed time |
| HTTPS figure server (port 7477) | Serves figure PNGs for vision-capable agents; base64 fallback in MCP response |

### 2.3 Agent Configuration

**`.mcp.json`** (project root — used by VS Code / RICA):
```json
{
  "mcpServers": {
    "hardware-um": {
      "type": "stdio",
      "command": "python",
      "args": ["-m", "app.mcp_server"],
      "env": {
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1"
      }
    }
  }
}
```

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

---

## 3. MCP Tool Contracts

### `search_um`

```python
search_um(query: str, chip_part: str, top_k: int = 6) -> list[Chunk] | dict
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
  "section_path": "§13. ICU > §13.2 Register Descriptions > §13.2.4 IELSRn",
  "page": 283,
  "render_text": "[§13.2.4 > IELSRn] bits 7:0 | IELS | R/W | reset 0 | Interrupt event link select",
  "citation": "【R01UH0890EJ0160 Rev.1.60 | §13.2.4 IELSRn | p.283】"
}
```

Returns `{"refusal": "No relevant content found in RA6M4 UM Rev.1.60."}` when top similarity score < 0.30.

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
  "address": "0x40006300",
  "size_bits": 32,
  "reset_value": "0x00000000",
  "access": "R/W",
  "section_path": "§13. ICU > §13.2 Register Descriptions > §13.2.4 IELSRn",
  "page": 283,
  "bit_fields": [
    {"bits": "31:9", "symbol": "—",    "access": "R",   "reset": "0", "description": "Reserved"},
    {"bits": "8",    "symbol": "IR",   "access": "R/W", "reset": "0", "description": "Interrupt status flag"},
    {"bits": "7:0",  "symbol": "IELS", "access": "R/W", "reset": "0", "description": "Interrupt event link select"}
  ],
  "citation": "【R01UH0890EJ0160 Rev.1.60 | §13.2.4 IELSRn | p.283】"
}
```

Returns `[]` for unknown names. Supports prefix matching for indexed variants (e.g. `IELSRn` matches `IELSR0`–`IELSR95`).

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
  "image_url": "https://localhost:7477/figures/R01UH0890EJ0160/p280_42.png",
  "image_data": "data:image/png;base64,...",
  "section_path": "§13. ICU > §13.1 Overview",
  "page": 280,
  "citation": "【R01UH0890EJ0160 Rev.1.60 | §13.1 Overview | p.280 | Figure 13.2】"
}
```

Returns `null` for unknown figure IDs.

---

## 4. Data Model

### 4.1 Chunk Metadata Envelope (10 fields)

| Field | Type | Description |
|---|---|---|
| `doc_id` | str | e.g. `R01UH0890EJ0160` |
| `revision` | str | e.g. `1.60` |
| `chip_part` | str | e.g. `RA6M4` |
| `section_path` | str | Resolved from TOC, e.g. `§13. ICU > §13.2 Register Descriptions > §13.2.4 IELSRn` |
| `page_start` | int | First page of the chunk |
| `page_end` | int | Last page of the chunk |
| `element_type` | str | `prose` \| `register_row` \| `figure` \| `table` |
| `peripheral` | str | e.g. `AGT`, `SCI`, `PORT` |
| `register_name` | str | e.g. `IELSRn`, `SCKCR` |
| `figure_id` | str | e.g. `Figure 13.2` |

### 4.2 SQLite Schema (`data/store/registers.db`)

**`registers` table** (511 rows for RA6M4 Rev.1.60):
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

**`bit_fields` table** (3,303 rows for RA6M4 Rev.1.60):
```sql
CREATE TABLE bit_fields (
    peripheral    TEXT,
    register_name TEXT,
    bits          TEXT,
    symbol        TEXT,
    access        TEXT,
    reset         TEXT,
    description   TEXT,
    FOREIGN KEY (peripheral, register_name) REFERENCES registers(peripheral, register_name)
);
```

### 4.3 Document Registry (`data/registry.json`)

```json
[
  {
    "doc_id":    "R01UH0890EJ0160",
    "revision":  "1.60",
    "chip_part": "RA6M4",
    "path":      "data/pdfs/r01uh0890ej0160-ra6m4.pdf"
  }
]
```

---

## 5. Module Map

| Module | File | Responsibility |
|---|---|---|
| `parser_text` | `ingest/parser_text.py` | PyMuPDF text + TOC + `section_path` resolver → `pages.jsonl` |
| `parser_figures` | `ingest/parser_figures.py` | Figure crop + caption pairing → `figures.jsonl` + PNG files |
| `parser_tables` | `ingest/parser_tables.py` | pdfplumber table detection (≥4 cols required) → `tables.jsonl` |
| `register_schema` | `ingest/register_schema.py` | Parse register tables → populate `registers.db` |
| `chunker` | `ingest/chunker.py` | Emit `prose`, `register_row`, `figure`, `table` chunks → `chunks.jsonl` |
| `indexer` | `ingest/indexer.py` | Embed chunks with sentence-transformers → persist to ChromaDB |
| `run_all` | `ingest/run_all.py` | Orchestrates all 6 ingest steps in order |
| `retriever` | `app/retriever.py` | Chroma retriever (top-k + similarity threshold guard + citation attach) |
| `register_tool` | `app/register_tool.py` | `register_lookup(name, chip_part)` via SQLite (exact + prefix match) |
| `figure_tool` | `app/figure_tool.py` | `get_figure(figure_id, chip_part)` via Chroma filter → base64 image |
| `figure_server` | `app/figure_server.py` | HTTPS daemon (port 7477, self-signed cert) serving PNG files |
| `mcp_server` | `app/mcp_server.py` | FastMCP server — exposes `search_um`, `register_lookup`, `get_figure` |

**Folder layout:**
```
project/
├── ingest/
│   ├── run_all.py
│   ├── parser_text.py
│   ├── parser_figures.py
│   ├── parser_tables.py
│   ├── register_schema.py
│   ├── chunker.py
│   └── indexer.py
├── app/
│   ├── mcp_server.py
│   ├── retriever.py
│   ├── register_tool.py
│   ├── figure_tool.py
│   └── figure_server.py
├── eval/
│   ├── run.py
│   ├── generate_testset.py
│   ├── golden_set_v2.csv
│   └── results.md
└── data/
    ├── registry.json
    ├── pdfs/
    ├── figures/{doc_id}/     ← extracted PNGs
    ├── parsed/
    │   ├── pages.jsonl
    │   ├── figures.jsonl
    │   ├── tables.jsonl
    │   └── chunks.jsonl
    └── store/
        ├── chroma/
        └── registers.db
```

---

## 6. Guardrails

### 6.1 Tool-Level Rules (enforced in MCP server, not by the agent)

| Rule | Enforcement point | Detail |
|---|---|---|
| Similarity threshold | `retriever.py` | Top score < 0.30 → return `{"refusal": "..."}`, not chunks |
| Top-k cap | `search_um` tool | Default k=6, hard max k=10 |
| Real figures only | `get_figure` tool | Returns `null` for any `figure_id` not in the indexed set |
| Deterministic registers | `register_lookup` tool | Returns SQLite record verbatim — no LLM interpretation |
| Citation baked in | All tools | Every returned item includes a pre-formatted `citation` field |
| Scope guard | `search_um` tool | Returns refusal dict for queries with no matches in the specified `chip_part` |
| 4-column register tables | `parser_tables.py` | Register tables must have ≥4 columns to be parsed — rejects false positives |

### 6.2 Suggested Agent System Prompt

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

## 7. Eval

### 7.1 Golden Set

- **File:** `eval/golden_set_v2.csv` (69 questions)
- **Distribution:** 30 `search_um` · 24 `register_lookup` · 15 `get_figure`
- **Generation:** `python -m eval.generate_testset` (requires LLM endpoint)
  - Track 1 (search_um): LLM generates questions from sampled prose/table chunks
  - Track 2 (register_lookup): Template-based, deterministic, no LLM
  - Track 3 (get_figure): LLM generates questions from figure captions
- **Note:** Generated questions are manually reviewed; questions where ground-truth chunk doesn't contain the answer are corrected or dropped.

### 7.2 Eval Runner

```bash
python -m eval.run                          # runs against golden_set_v2.csv
python -m eval.run --golden eval/my.csv    # custom golden set
```

Calls MCP tools directly (no agent in the loop). Scores pass/fail per question, writes `eval/results.md`.

### 7.3 Pass Criteria

| Metric | Target | Current |
|---|---|---|
| Golden set pass rate | ≥ 80% | **100%** (69/69) |
| `register_lookup` pass rate | 100% | **100%** |
| `get_figure` pass rate | ≥ 90% | **100%** |
| `search_um` pass rate | ≥ 75% | **100%** (30/30) |

### 7.4 Failure Categories

| Code | Meaning |
|---|---|
| `wrong_section` | Returned chunk's section does not match expected |
| `wrong_page` | Page outside expected range |
| `hallucinated_register` | Register tool returned record with empty `bit_fields` |
| `missing_citation` | Returned chunk has no `citation` field |
| `false_refusal` | Tool returned refusal for a query that had sufficient matching content |
