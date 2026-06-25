# POC Tasks — RAG over Hardware User Manuals (MCP Agent Interface)

*Part of: [POC_RAG_Hardware_UM_Plan.md](POC_RAG_Hardware_UM_Plan.md)*

> Execute tasks **in order**. Verify each checkpoint before proceeding. **Stop and report** if a checkpoint fails.

---

## Task 0 — Repo Bootstrap

**Actions:**
1. Create folders: `ingest/`, `app/`, `eval/`, `data/pdfs/`, `data/figures/`, `data/parsed/`, `data/store/`
2. Create `requirements.txt` (see [Prerequisites in Spec](POC_RAG_Spec.md#1-prerequisites))
3. Create `.env` with `OPENAI_API_KEY=...`
4. Install dependencies

**Checkpoint:** `python -c "import pymupdf, pdfplumber, chromadb, langchain"` exits 0.

---

## Task 1 — Register the UM

**Actions:**
1. Copy `r01uh0890ej0150-ra6m4.pdf` to `data/pdfs/`
2. Create `data/registry.json` (see [§3.3 in Spec](POC_RAG_Spec.md#33-document-registry-dataregistryjson))

**Checkpoint:** `registry.json` is valid JSON; `pymupdf.open(...)` reports > 1500 pages.

---

## Task 2 — Parse Text + TOC

**Actions:**
Implement `ingest/parser_text.py` → emit `data/parsed/pages.jsonl`, one record per text block:
```json
{"page": 51, "bbox": [...], "text": "...", "section_path": "§13 > §13.2"}
```
Use `doc.get_toc()` to assign `section_path` (longest TOC entry whose page ≤ current page).

**Checkpoint:** ≥ 95% of blocks on pages 50–100 have non-null `section_path`; print 3 random samples.

---

## Task 3 — Detect Register Tables

**Actions:**
Implement `ingest/parser_tables.py` using pdfplumber. For each page run `extract_tables()`. Classify as a register table when the header row matches ≥ 3 of:

`{Bit, Bit Name, Symbol, Value, R/W, Reset, Description}` (case-insensitive)

Emit raw table JSON to `data/parsed/tables.jsonl`.

**Checkpoint:** ≥ 50 register tables detected; spot-check that known registers (`SCKCR`, `IELSRn`, `PORT0.PCNTR1`) appear.

---

## Task 4 — Build Register Schema + SQLite

**Actions:**
Implement `ingest/register_schema.py`. For each register table:
1. Parse the header block immediately above (register name, address, reset, access) from prose
2. Merge with bit-field rows
3. Persist to `data/store/registers.db` using the schema in [§3.2 in Spec](POC_RAG_Spec.md#32-sqlite-schema-registersdb)

**Checkpoint:** `SELECT COUNT(*) FROM registers` ≥ 50; `register_lookup("SCKCR")` returns a record with non-empty `bit_fields`.

---

## Task 5 — Extract Figures + VLM Captions

**Actions:**
Implement `ingest/parser_figures.py`:

1. For each page, call `page.get_images(full=True)`; save each to `data/figures/{doc_id}/p{page}_{xref}.png` (skip images < 64×64 px)
2. Search same-page text blocks for nearest match of `^(Figure|Fig\.)\s+\d+\.\d+\b`; record `figure_id` + caption
3. Call `gpt-4o-mini` vision once per image with this prompt:
   ```
   This is a figure from a semiconductor chip Hardware User's Manual.
   In ≤4 sentences, describe:
   1) figure type (block / timing / pin-out / schematic / state machine / waveform / table-as-image / other)
   2) main blocks, signals, pins, or peripherals labeled
   3) obvious relationships (arrows, buses, clock paths, hierarchy)
   4) any verbatim text labels you can read
   Do NOT invent labels. If unsure, say "unclear".
   Return JSON: {"type":"...","summary":"...","labels_seen":[...]}
   ```
4. Cache by SHA-256 of image bytes in `data/figures/cache.json`
5. Emit one figure chunk: `render_text = "[{section_path} > {figure_id}] {caption}. {vlm_summary}"`

**Checkpoint:** ≥ 90% of extracted images have a paired `figure_id`; cache file populated; cost report printed (target < $5 for one UM).

---

## Task 6 — Chunking

**Actions:**
Implement `ingest/chunker.py` producing three chunk types:

| Type | Strategy | `render_text` format |
|---|---|---|
| `prose` | `RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)` over section bodies grouped by `section_path` | chunk text prefixed with `[section_path]` |
| `register_row` | One per bit-field row | `[{section_path} > {register_name}] bits {bits} \| {symbol} \| {access} \| reset {reset} \| {description}` |
| `figure` | One per extracted figure (from Task 5) | `[{section_path} > {figure_id}] {caption}. {vlm_summary}` |

Write all chunks to `data/parsed/chunks.jsonl` with the full 10-field metadata envelope (see [§3.1 in Spec](POC_RAG_Spec.md#31-chunk-metadata-envelope-10-fields)).

**Checkpoint:** `chunks.jsonl` contains all three `element_type` values; total chunk count printed.

---

## Task 7 — Embed + Index in Chroma

**Actions:**
Implement `ingest/indexer.py`:
- Use `langchain_openai.OpenAIEmbeddings(model="text-embedding-3-small")`
- Persist to `data/store/chroma` via `langchain_community.vectorstores.Chroma`
- Batch embed 100 chunks at a time

**Checkpoint:** `Chroma(...).similarity_search("clock generation circuit", k=3)` returns ≥ 1 hit from the Clock Generation Circuit section.

---

## Task 8 — Register Lookup Tool

**Actions:**
Implement `app/register_tool.py`:
- `register_lookup(name: str, chip_part: str) -> list[dict]` — returns a list because cross-peripheral name collisions are possible
- Queries `data/store/registers.db`; filters by `chip_part` via the `doc_id` join on `data/registry.json`
- Attaches a `citation` field to each returned record

**Checkpoint:** Returns expected structured record for `IELSRn` with non-empty `bit_fields` and a `citation`; returns `[]` for an unknown name; `register_lookup("SCKCR", "RA6M4")` returns ≥ 1 record.

---

## Task 9 — Retriever + Figure Tool

**Actions:**

**`app/retriever.py`**
- Wrap `Chroma.as_retriever(search_kwargs={"k": 6, "filter": {"chip_part": chip_part}})`
- After retrieval, check top similarity score; if < 0.30 return the refusal string:
  `"No relevant content found in {chip_part} UM Rev.{revision}."`
- Attach a pre-formatted `citation` field to every returned chunk:
  `【{doc_id} Rev.{revision} | §{section_path} | p.{page}】`

**`app/figure_tool.py`**
- `get_figure(figure_id: str, chip_part: str) -> dict | None`
- Query Chroma with `filter={"figure_id": figure_id, "chip_part": chip_part}`
- Return `FigureRecord` (see [§3 in Spec](POC_RAG_Spec.md#3-mcp-tool-contracts)) or `None`

**Checkpoint:** Direct Python calls:
- `retriever.search("clock generation circuit", chip_part="RA6M4")` returns ≥ 1 chunk with a `citation` field
- `get_figure("Figure 13.2", chip_part="RA6M4")` returns a non-null record with `image_path` and `citation`
- `retriever.search("xyzzy gibberish", chip_part="RA6M4")` returns the refusal string

---

## Task 10 — MCP Server

**Actions:**
Implement `app/mcp_server.py` using `fastmcp`:

```python
from fastmcp import FastMCP
mcp = FastMCP("hardware-um")

@mcp.tool()
def search_um(query: str, chip_part: str, top_k: int = 6) -> list[dict]:
    """Search the Hardware User Manual for prose, register, or figure content."""
    ...

@mcp.tool()
def register_lookup(name: str, chip_part: str) -> list[dict]:
    """Look up a register by name. Returns address, reset value, and all bit fields."""
    ...

@mcp.tool()
def get_figure(figure_id: str, chip_part: str) -> dict | None:
    """Retrieve a figure by its ID (e.g. 'Figure 13.2'). Returns caption, VLM summary, and image path."""
    ...

if __name__ == "__main__":
    mcp.run()  # stdio transport by default
```

Add agent config files (see [§2.3 in Spec](POC_RAG_Spec.md#23-agent-configuration-local-demo)):
- `claude_desktop_config_snippet.json` — snippet to paste into Claude Desktop config
- `.vscode/mcp.json` — VS Code Copilot Chat MCP config

**Checkpoint:**
1. `python -m app.mcp_server` starts without error
2. Using the MCP inspector (`npx @modelcontextprotocol/inspector python -m app.mcp_server`), call each tool manually:
   - `search_um("What does IELSRn.IELS select?", "RA6M4")` → returns chunks citing §13.2.4, page in the 280s
   - `register_lookup("SCKCR", "RA6M4")` → returns record with address and `bit_fields`
   - `get_figure("Figure 13.2", "RA6M4")` → returns caption and `image_path`
3. Add server to Claude Desktop config; confirm the 3 tools appear in Claude's tool list

---

## Task 11 — Golden Set & Smoke Eval

**Actions:**
1. Create `eval/golden_set.csv` with 40 rows:

   | Column | Description |
   |---|---|
   | `question` | The test question |
   | `tool` | `search_um` \| `register_lookup` \| `get_figure` |
   | `expected_section` | e.g. `§13.2.4` |
   | `expected_page_range` | e.g. `280-285` |
   | `expected_register` | e.g. `IELSRn` |
   | `expected_figure_id` | e.g. `Figure 13.2` |

   Distribution: 10 prose · 10 register · 10 figure · 10 cross-section

2. Implement `eval/run.py`: call each MCP tool **directly** (no agent in the loop), score pass/fail by:
   - Presence of expected `section_path` substring in returned chunks
   - Page within `expected_page_range`
   - For registers: `register_name` present in returned record
   - For figures: correct `figure_id` in returned record

**Checkpoint:** Pass rate ≥ 80%; failures categorized in `eval/results.md` using the categories in [§7 of Spec](POC_RAG_Spec.md#7-eval-criteria).

---

## Task 12 (Stretch) — Second UM Smoke Test

**Actions:**
1. Add `r01uh1064ej0110-ra8p1.pdf` to `data/pdfs/`
2. Append to `registry.json` with `chip_part="RA8P1"`
3. Re-run ingestion
4. Verify the UI chip picker switches collections cleanly

**Checkpoint:** Both chips selectable in the UI; the 3 demo questions work on RA8P1 with citations to RA8P1 only. No cross-UM synthesis.
