# POC Spec — RAG over Hardware User Manuals (MCP Agent Interface)

*Part of: [POC_RAG_Hardware_UM_Plan.md](POC_RAG_Hardware_UM_Plan.md)*

---

## 1. Prerequisites

**Python:** 3.11+

**Dependencies (`requirements.txt`):**
```
chromadb
langchain
langchain-community
langchain-chroma
langchain-text-splitters
langchain-huggingface
sentence-transformers
beautifulsoup4
sqlite-utils
rank_bm25
mcp
fastmcp
pydantic
python-dotenv
ragas>=0.2
rapidfuzz
```
> PDF-specific dependencies (`pymupdf`, `pdfplumber`) are no longer required for chips that have a Smart Manual DB.

**Environment (`.env`):**
```
EMBED_MODEL=sentence-transformers/all-MiniLM-L6-v2
HF_HUB_OFFLINE=1                                 # fully offline at serve-time
```

**Models:**
| Purpose | Model |
|---|---|
| Embeddings (ingest + serve) | `sentence-transformers/all-MiniLM-L6-v2` (local, offline) |

> No LLM call at serve-time — the calling agent provides reasoning. The MCP server returns raw retrieved data only.

**Data source:** Smart Manual DB for `RA6M4`, located at:
```
%APPDATA%\Code\User\globalStorage\renesaselectronicscorporation.renesas-smart-manual\downloads\RA6M4\RA6M4_en
```
Plain SQLite file — no fallback path. If it's missing, the tool returns an error rather than silently falling back to a PDF.

---

## 2. Architecture

### 2.1 Ingestion (offline, one-shot — prose & figures only)

```
Smart Manual DB (SQLite: RA6M4_en)
  │
  ├─▶ freeWord.keyword ─────────────────────────────────────┐
  │     clean plain text per section                        │
  │                                                          │
  ├─▶ <figure>/<svg> blocks in display_data (HTML) ──────────┤
  │     freeWord + registerList + bitList                   │
  │         │                                                │
  │         ▼                                                ▼
  │    prose chunks                                    figure chunks
  │    (500 chars, 80 overlap)                          + SVG saved to disk
  │         │                                            (data/figures/{chip}/)
  │         └──────────────────┬─────────────────────────────┘
  │                            ▼
  │         sentence-transformers/all-MiniLM-L6-v2 (local)
  │                            ▼
  └──────────────────▶ ChromaDB persistent collection
```

Registers and bit-fields are **not** part of this ingestion step — they're queried live at request time (see 2.2).

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
  │           └─▶ returns: list of Chunk {section_title, render_text, citation}
  │
  ├─▶ tool: register_lookup(name, chip_part)
  │     └─▶ app/smart_manual_locator.py resolves DB path for chip_part
  │           └─▶ live SQLite query against registerList/bitList
  │                 └─▶ BeautifulSoup parses display_data HTML for R/W, reset, enum values
  │                       └─▶ returns: list of RegisterRecord {address, reset, bit_fields, citation}
  │
  └─▶ tool: get_figure(figure_id, chip_part)
        └─▶ Chroma filter by figure_id + metadata
              └─▶ returns: FigureRecord {caption, svg as data URI, section_title, citation}

Agent receives tool results → reasons → answers developer's question
```

**Key design decisions:**

| Decision | Rationale |
|---|---|
| Smart Manual DB as the single data source | Already-structured, already-downloaded, avoids re-parsing a 1900-page PDF |
| Registers queried live, no import step | Data is already normalized SQLite — copying it into a second DB adds no value |
| No fallback in the locator | Keeps the tool honest about what chip data is actually available locally |
| MCP server as sole interface | Agent calls tools mid-conversation — no UI context-switching |
| No LLM in the server | The calling agent provides reasoning; the server provides only retrieved facts |
| Local sentence-transformers embeddings | Fully offline at serve-time — no external API dependency |
| Single Chroma collection (prose + figure) | One retrieval hop, simpler metadata filtering |
| Citations baked into tool responses | Every returned item carries a `citation` field the agent can't lose |
| Similarity threshold enforced at tool level | Returns a refusal dict rather than low-confidence chunks |
| Hybrid BM25 + dense retrieval (RRF) | Prevents dense-embedding collisions between similarly named registers |
| Figures kept as native SVG | Source figures are vector (`<svg>`) — rasterizing would lose fidelity |
| No page numbers in citations | Smart Manual DB carries no page metadata; citations reference section titles instead |

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
  "element_type": "prose | figure",
  "section_title": "13.2.4. IELSRn : ICU Event Link Setting Register n",
  "render_text": "[13.2.4. IELSRn] Interrupt event link select ...",
  "citation": "【RA6M4 Smart Manual | 13.2.4. IELSRn】"
}
```

Returns `{"refusal": "No relevant content found in RA6M4 Smart Manual."}` when top similarity score < 0.30.

---

### `register_lookup`

```python
register_lookup(name: str, chip_part: str) -> list[RegisterRecord]
```

Returns list (multiple records when the name matches several registers, e.g. an indexed family):
```json
{
  "peripheral": "R_ICU",
  "register_name": "IELSR0",
  "address": "0x40006300",
  "access": "R/W",
  "bit_fields": [
    {"bits": "31:9", "symbol": "—",    "access": "R",   "reset": "0", "description": "Reserved"},
    {"bits": "8",    "symbol": "IR",   "access": "R/W", "reset": "0", "description": "Interrupt status flag"},
    {"bits": "7:0",  "symbol": "IELS", "access": "R/W", "reset": "0", "description": "Interrupt event link select"}
  ],
  "citation": "【RA6M4 Smart Manual | IELSR0 : ICU Event Link Setting Register 0】"
}
```

Returns `[]` for unknown names. Supports prefix matching for indexed families (e.g. `IELSRn` matches `IELSR0`–`IELSR95`, each individually addressed in the Smart Manual DB).

> Register names follow the Smart Manual's FSP naming convention, which can differ from the PDF (e.g. `SCKDIVCR`, not the PDF's `SCKCR`).

---

### `get_figure`

```python
get_figure(figure_id: str, chip_part: str) -> FigureRecord | None
```

```json
{
  "figure_id": "Figure 13.2",
  "caption": "ICU Block Diagram",
  "image_svg": "<svg ...>...</svg>",
  "section_title": "13.1. Overview",
  "citation": "【RA6M4 Smart Manual | 13.1. Overview | Figure 13.2】"
}
```

Returns `null` for unknown figure IDs.

---

## 4. Data Model

### 4.1 Chunk Metadata Envelope

| Field | Type | Description |
|---|---|---|
| `chip_part` | str | e.g. `RA6M4` |
| `section_title` | str | From `freeWord.title`, e.g. `13.2.4. IELSRn : ICU Event Link Setting Register n` |
| `element_type` | str | `prose` \| `figure` |
| `figure_id` | str | e.g. `Figure 13.2` (figure chunks only) |

No page numbers — the Smart Manual DB does not carry page metadata.

### 4.2 Register Data (no local copy)

`register_lookup` queries the Smart Manual DB directly at request time — there is no `registers.db` or import step. Relevant source tables:

| Table | Role |
|---|---|
| `moduleList` | Peripheral base addresses |
| `registerList` | Register definitions + bit-diagram HTML (`display_data`) |
| `bitList` | Bit-field definitions + R/W and enum values, only present inside `display_data` HTML |

`app/register_tool.py` parses `display_data` with BeautifulSoup to extract R/W, reset, and enumerated values that aren't available as plain columns. Full schema notes: [SmartManual_DB_Analysis.md](SmartManual_DB_Analysis.md).

---

## 5. Module Map

| Module | File | Responsibility |
|---|---|---|
| `smart_manual_locator` | `app/smart_manual_locator.py` | Resolve `{chip_part}` → local Smart Manual DB path (no fallback) |
| `register_tool` | `app/register_tool.py` | `register_lookup(name, chip_part)` — live SQLite query + BeautifulSoup HTML parse |
| `parser_smart_manual_text` | `ingest/parser_smart_manual_text.py` | `freeWord.keyword` → `pages_sm.jsonl` |
| `parser_smart_manual_figures` | `ingest/parser_smart_manual_figures.py` | Extract `<figure>`/`<svg>` blocks → SVG files + figure records |
| `chunker` | `ingest/chunker.py` | Emit `prose` and `figure` chunks → `chunks.jsonl` (unchanged) |
| `indexer` | `ingest/indexer.py` | Embed chunks with sentence-transformers → persist to ChromaDB (unchanged) |
| `run_all` | `ingest/run_all.py` | Orchestrates the ingest steps in order |
| `retriever` | `app/retriever.py` | Chroma retriever (top-k + similarity threshold guard + citation attach) |
| `figure_tool` | `app/figure_tool.py` | `get_figure(figure_id, chip_part)` via Chroma filter → SVG |
| `figure_server` | `app/figure_server.py` | HTTPS daemon (port 7477) serving SVG files |
| `mcp_server` | `app/mcp_server.py` | FastMCP server — exposes `search_um`, `register_lookup`, `get_figure` |

**Folder layout:**
```
project/
├── ingest/
│   ├── run_all.py
│   ├── parser_smart_manual_text.py
│   ├── parser_smart_manual_figures.py
│   ├── chunker.py
│   └── indexer.py
├── app/
│   ├── mcp_server.py
│   ├── retriever.py
│   ├── smart_manual_locator.py
│   ├── register_tool.py
│   ├── figure_tool.py
│   └── figure_server.py
├── eval/
│   ├── run.py
│   ├── generate_testset.py
│   ├── golden_set_v2.csv
│   └── results.md
└── data/
    ├── figures/{chip}/       ← extracted SVGs
    ├── parsed/
    │   ├── pages_sm.jsonl
    │   └── chunks.jsonl
    └── store/
        └── chroma/
```

---

## 6. Guardrails

### 6.1 Tool-Level Rules (enforced in MCP server, not by the agent)

| Rule | Enforcement point | Detail |
|---|---|---|
| Similarity threshold | `retriever.py` | Top score < 0.30 → return `{"refusal": "..."}`, not chunks |
| Top-k cap | `search_um` tool | Default k=6, hard max k=10 |
| Real figures only | `get_figure` tool | Returns `null` for any `figure_id` not in the indexed set |
| Deterministic registers | `register_lookup` tool | Returns the Smart Manual DB record verbatim — no LLM interpretation |
| Citation baked in | All tools | Every returned item includes a pre-formatted `citation` field |
| Scope guard | `search_um` tool | Returns refusal dict for queries with no matches in the specified `chip_part` |
| No fallback on missing DB | `smart_manual_locator.py` | Raises/returns an explicit error if the chip's Smart Manual DB isn't found locally |

### 6.2 Suggested Agent System Prompt

```
You have access to hardware UM tools for the {chip_part} chip.

When answering questions about registers, peripherals, or figures:
1. Always call the appropriate tool first — do not answer from memory.
2. Quote register addresses, reset values, and bit positions verbatim from register_lookup results.
3. Cite every factual statement using the citation field returned by the tool.
4. If a tool returns a refusal or null, tell the user the information is not available.
5. Do not generate driver code or register configuration sequences.
```

---

## 7. Eval

- **File:** `eval/golden_set_v2.csv`
- Built for the previous PDF-based pipeline — register names and citations need updating for the Smart Manual DB's FSP naming (e.g. `SCKCR` → `SCKDIVCR`) and the removal of page numbers before it's valid again.
- **Generation:** `python -m eval.generate_testset` (requires LLM endpoint)
- **Run:** `python -m eval.run`
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
