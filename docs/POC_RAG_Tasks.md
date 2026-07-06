# POC Tasks — RAG over Hardware User Manuals (MCP Agent Interface)

*Part of: [POC_RAG_Hardware_UM_Plan.md](POC_RAG_Hardware_UM_Plan.md)*

> Replaces the earlier PDF-parsing task list. Registers/bit-fields, prose, and figures are now all sourced from the Smart Manual DB instead of the PDF.

---

## Task 0 — Repo Bootstrap ✅

**Actions:**
1. Create folders: `ingest/`, `app/`, `eval/`, `data/figures/`, `data/parsed/`, `data/store/`
2. Create `requirements.txt`
3. Create `.env` with `EMBED_MODEL`, `HF_HUB_OFFLINE=1`
4. Install dependencies: `pip install -r requirements.txt`

**Checkpoint:** `python -c "import chromadb, langchain, fastmcp, bs4"` exits 0.

---

## Task 1 — Smart Manual DB Locator ⬜

**Actions:**
Implement `app/smart_manual_locator.py`:
- `locate(chip_part: str) -> Path` — resolves to
  `%APPDATA%\Code\User\globalStorage\renesaselectronicscorporation.renesas-smart-manual\downloads\{chip_part}\{chip_part}_en`
- No fallback — raise/return an explicit error if the file doesn't exist.

**Checkpoint:** `locate("RA6M4")` returns a path to a valid SQLite file (`sqlite3.connect(...).execute("select 1")` succeeds).

---

## Task 2 — Register Lookup Tool (live query) ⬜

**Actions:**
Rewrite `app/register_tool.py`:
1. Use the locator to open a connection to `RA6M4_en` at request time
2. Query `registerList` for exact + prefix matches (`LIKE 'name%'`) on `register_symbol_name`
3. Join `bitList` on `register_symbol_name`; parse each row's `display_data` HTML with BeautifulSoup to extract R/W, reset, and enumerated values (not present as plain columns)
4. Attach a citation (no page number): `【{chip_part} Smart Manual | {register_name}】`

**Checkpoint:** `register_lookup("IELSR0", "RA6M4")` returns a record with non-empty `bit_fields`; `register_lookup("SCKDIVCR", "RA6M4")` succeeds (note: PDF's `SCKCR` no longer applies).

---

## Task 3 — Prose Ingestion ⬜

**Actions:**
Implement `ingest/parser_smart_manual_text.py`:
1. Read all rows from `freeWord` (`title`, `keyword`)
2. Emit one record per row to `data/parsed/pages_sm.jsonl`: `{"section_title": title, "text": keyword}`

**Checkpoint:** `pages_sm.jsonl` row count matches `freeWord` row count (2,089 for RA6M4).

---

## Task 4 — Figure Ingestion ⬜

**Actions:**
Implement `ingest/parser_smart_manual_figures.py`:
1. Scan `display_data` HTML in `freeWord`, `registerList`, and `bitList` for `<figure>` blocks
2. For each, extract the `<figcaption>` text and the inner `<svg>...</svg>`
3. Save the SVG to `data/figures/{chip_part}/{figure_id}.svg`
4. Emit one figure chunk per figure: `render_text = "[{section_title} > {figure_id}] {caption}"`

**Checkpoint:** SVG files present in `data/figures/RA6M4/`; each figure chunk has a non-empty caption.

---

## Task 5 — Chunking + Embedding ⬜

**Actions:**
- `ingest/chunker.py` — reuse existing prose chunking (`RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)`) over `pages_sm.jsonl`; one chunk per figure record. No code changes needed beyond pointing it at the new input files.
- `ingest/indexer.py` — reuse existing embedding + Chroma persistence, unchanged.

**Checkpoint:** `Chroma(...).similarity_search("clock generation circuit", k=3)` returns ≥ 1 relevant hit.

---

## Task 6 — Figure Tool + Server (SVG) ⬜

**Actions:**
- `app/figure_tool.py` — `get_figure(figure_id, chip_part)` via Chroma filter, reads the `.svg` file, returns it as an `image/svg+xml` data URI
- `app/figure_server.py` — serve `data/figures/{chip}/*.svg` with the correct MIME type

**Checkpoint:** `get_figure("Figure 13.2", "RA6M4")` returns a valid SVG payload.

---

## Task 7 — MCP Server ⬜

**Actions:**
Confirm `app/mcp_server.py` still exposes `search_um`, `register_lookup`, `get_figure` with the same signatures — no agent-facing changes needed, only the implementations underneath change.

**Checkpoint:**
1. `python -m app.mcp_server` starts without error
2. All three tools callable via MCP inspector

---

## Task 8 — Update Golden Set + Re-run Eval ⬜

**Actions:**
1. Update `eval/golden_set_v2.csv`: fix renamed registers (e.g. `SCKCR` → `SCKDIVCR`), drop page-number expectations, update figure IDs if needed
2. `python -m eval.run`

**Checkpoint:** Pass rate reported in `eval/results.md`; investigate any drop vs. the previous PDF-based baseline (100%, 69/69).
