# POC Tasks — RAG over Hardware User Manuals (MCP Agent Interface)

*Part of: [POC_RAG_Hardware_UM_Plan.md](POC_RAG_Hardware_UM_Plan.md)*

> All tasks below are **complete** for RA6M4 Rev.1.60. This document serves as a record and as a guide for adding a second UM.

---

## Task 0 — Repo Bootstrap ✅

**Actions:**
1. Create folders: `ingest/`, `app/`, `eval/`, `data/pdfs/`, `data/figures/`, `data/parsed/`, `data/store/`
2. Create `requirements.txt`
3. Create `.env` with `OPENAI_API_BASE`, `OPENAI_API_KEY`, `OPENAI_MODEL`, `EMBED_MODEL`, `HF_HUB_OFFLINE=1`
4. Install dependencies: `pip install -r requirements.txt`

**Checkpoint:** `python -c "import pymupdf, pdfplumber, chromadb, langchain, fastmcp"` exits 0.

---

## Task 1 — Register the UM ✅

**Actions:**
1. Copy `r01uh0890ej0160-ra6m4.pdf` to `data/pdfs/`
2. Create `data/registry.json`:
```json
[{
  "doc_id": "R01UH0890EJ0160",
  "revision": "1.60",
  "chip_part": "RA6M4",
  "path": "data/pdfs/r01uh0890ej0160-ra6m4.pdf"
}]
```

**Checkpoint:** `registry.json` is valid JSON; `pymupdf.open(...)` reports > 1900 pages.

---

## Task 2 — Parse Text + TOC ✅

**Actions:**
Implement `ingest/parser_text.py` → emit `data/parsed/pages.jsonl`.

Uses `doc.get_toc()` to assign `section_path` (longest TOC entry whose page ≤ current page). Each record:
```json
{"page": 283, "bbox": [...], "text": "...", "section_path": "§13. ICU > §13.2 Register Descriptions > §13.2.4 IELSRn"}
```

**Checkpoint:** ≥ 95% of blocks on sampled pages have non-null `section_path`.

---

## Task 3 — Detect Register Tables ✅

**Actions:**
Implement `ingest/parser_tables.py` using pdfplumber. Classify as a register table when:
- ≥ **4 columns** (enforced to avoid false positives on narrow tables)
- Header row matches ≥ 3 of: `{Bit, Bit Name, Symbol, Value, R/W, Reset, Description}` (case-insensitive)

Emit raw table JSON to `data/parsed/tables.jsonl`.

**Checkpoint:** Known registers (`SCKCR`, `IELSRn`) detected; spot-check bit field rows.

---

## Task 4 — Build Register Schema + SQLite ✅

**Actions:**
Implement `ingest/register_schema.py`. For each register table:
1. Parse the header block immediately above (register name, address, reset, access) from prose
2. Merge with bit-field rows
3. Persist to `data/store/registers.db`

**Result:** 511 registers · 3,303 bit fields.

**Checkpoint:** `SELECT COUNT(*) FROM registers` = 511; `register_lookup("SCKCR", "RA6M4")` returns record with non-empty `bit_fields`.

---

## Task 5 — Extract Figures ✅

**Actions:**
Implement `ingest/parser_figures.py`:

1. For each page, detect figure bounding boxes via vector drawings + `^(Figure|Fig\.)\s+\d+\.\d+\b` caption regex
2. Crop and save as `data/figures/{doc_id}/p{page}_{idx}.png`
3. Emit one figure chunk per image: `render_text = "[{section_path} > {figure_id}] {caption}"`

> **Note:** VLM captioning (gpt-4o-mini) was evaluated but removed — figure chunks use caption text only. The `vlm_summary` field is preserved in the schema for future use.

**Checkpoint:** ≥ 90% of extracted figures paired with a `figure_id`; PNGs present in `data/figures/`.

---

## Task 6 — Chunking ✅

**Actions:**
Implement `ingest/chunker.py` producing four chunk types:

| Type | Strategy | `render_text` format |
|---|---|---|
| `prose` | `RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)` over section bodies | `[section_path] chunk text` |
| `register_row` | One per bit-field row from `tables.jsonl` | `[section_path > register_name] bits {bits} \| {symbol} \| {access} \| reset {reset} \| {description}` |
| `figure` | One per extracted figure | `[section_path > figure_id] caption` |
| `table` | One per non-register table | raw table text prefixed with `[section_path]` |

Output: `data/parsed/chunks.jsonl` with full 10-field metadata envelope.

**Checkpoint:** `chunks.jsonl` contains all four `element_type` values; total chunk count printed.

---

## Task 7 — Embed + Index in Chroma ✅

**Actions:**
Implement `ingest/indexer.py`:
- Use `langchain_huggingface.HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")`
- Persist to `data/store/chroma` via `langchain_chroma.Chroma`
- Batch embed 100 chunks at a time

**Checkpoint:** `Chroma(...).similarity_search("clock generation circuit", k=3)` returns ≥ 1 hit from the Clock Generation Circuit section.

---

## Task 8 — Register Lookup Tool ✅

**Actions:**
Implement `app/register_tool.py`:
- `register_lookup(name: str, chip_part: str) -> list[dict]`
- Exact match + prefix match for indexed variants (e.g. `IELSRn` → `IELSR0`–`IELSR95`)
- Uses `rapidfuzz` for fuzzy name normalization
- Attaches `citation` field to each returned record

**Checkpoint:** Returns expected structured record for `IELSRn`; returns `[]` for unknown name.

---

## Task 9 — Retriever + Figure Tool ✅

**Actions:**

**`app/retriever.py`**
- Wrap `Chroma` with `search_type="similarity_score_threshold"` or custom score check
- Top similarity score < 0.30 → return refusal string
- Attach pre-formatted `citation` field to every returned chunk

**`app/figure_tool.py`**
- `get_figure(figure_id: str, chip_part: str) -> dict | None`
- Query Chroma with `filter={"figure_id": figure_id, "chip_part": chip_part}`
- Read PNG from disk, encode as base64 data URI for MCP response

**`app/figure_server.py`**
- HTTPS daemon on port 7477 (self-signed cert auto-generated at startup)
- Serves `data/figures/{doc_id}/*.png` at `https://localhost:7477/figures/{doc_id}/{filename}`

---

## Task 10 — MCP Server ✅

**Actions:**
Implement `app/mcp_server.py` using `fastmcp`:
- Eager-loads embedding model and vectorstore at startup (avoids first-call timeout)
- Starts HTTPS figure server on port 7477 in daemon thread
- Exposes `search_um`, `register_lookup`, `get_figure` as MCP tools

Config files:
- `.mcp.json` — project-level MCP config for VS Code / RICA IDE
- `.rica/mcpServers/hardware-um.yaml` — RICA-specific config

**Checkpoint:**
1. `python -m app.mcp_server` starts without error
2. All three tools callable via MCP inspector
3. Tools appear in IDE agent tool list

---

## Task 11 — Golden Set & Eval ✅

**Actions:**
1. `eval/generate_testset.py` — generates `golden_set_v2.csv` (69 questions):
   - Track 1 (30 q): LLM-generated from prose/table chunks
   - Track 2 (24 q): Template-based register questions (deterministic)
   - Track 3 (15 q): LLM-generated from figure captions
2. Manual review of generated questions — corrected 3 rows, dropped 1 ambiguous question
3. `eval/run.py` — calls tools directly, scores pass/fail, writes `eval/results.md`

**Result:** **94% pass rate** (65/69). See `eval/results.md` for breakdown.

**Run eval:** `python -m eval.run`

---

## Task 12 (Stretch) — Second UM Smoke Test

**Actions:**
1. Add a second PDF to `data/pdfs/`
2. Append entry to `data/registry.json` with new `chip_part` and `doc_id`
3. Re-run ingestion: `python -m ingest.run_all`
4. Verify `register_lookup("SCKCR", "<new_chip>")` returns records citing the new UM only

**Checkpoint:** Both chips queryable; citations reference correct UM; no cross-UM bleed.
