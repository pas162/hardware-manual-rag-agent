# POC Tasks — RAG over Hardware User Manuals (MCP Agent Interface)

*Part of: [POC_RAG_Hardware_UM_Plan.md](POC_RAG_Hardware_UM_Plan.md)*

> Replaces the earlier PDF-parsing task list. Registers/bit-fields, prose, and figures are now all sourced from the Smart Manual DB instead of the PDF.

---

## Task 0 — Repo Bootstrap ✅

**Actions:**
1. Create folders: `ingest/`, `app/`, `eval/`, `data/parsed/`, `data/store/`
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

## Task 3 — Prose + General-Table Ingestion ⬜

> **Revised after inspecting the live DB.** `freeWord.keyword` is *not* clean prose — it's a flattened text soup that inlines register bit-table numbers/labels and SVG figure-label text into the surrounding prose with no separators. Verified on RA6M4: e.g. the `IELSRn` section's `keyword` reads `...Value after reset: 0 0 0 0...Bit Symbol Function R/W 8:0 IELS[8:0]...`, and the `1.2. Block Diagram` section's `keyword` reads `...Memory Memory 1 MB code flash 1 MB code flash 8 KB data flash...` (every text label inside that figure's `<svg>`, flattened). Using `keyword` as-is would dilute `search_um` with table/diagram-label noise.
>
> Parsing `freeWord.display_data` (HTML) instead lets us split cleanly — but naively stripping *all* `<table>` tags is also wrong: of the 2,089 `freeWord` rows, 1,020 contain a `<table>`, and **392 of those are not register-titled sections** — e.g. `1.4. Function Comparison`, `1.5. Pin Functions`, `2.7.2. Peripheral Address Map`. These are genuine lookup tables with no equivalent in `register_lookup`; discarding them would silently lose real content. Only register bit-tables (redundant with `register_lookup`) should be dropped.

**Actions:**
Implement `ingest/parser_smart_manual_text.py`:
1. Read `title`, `display_data` from every `freeWord` row via the locator.
2. Parse `display_data` with BeautifulSoup. For each `<table>`, classify it:
   - **register_table** — its `<th>` header row starts with "Bit" (the `Bit | Symbol | Function | R/W` layout used throughout `bitList`), OR it's the borderless bit-position diagram (`<table class="frame-none">`, no `<th>` at all) inside a register-titled section.
   - **general_table** — everything else (function comparison, pin lists, address maps, etc.)
3. `.decompose()` every `register_table` and every `<figure>` block (figures are handled by Task 4). Leave `general_table`s in the tree.
4. Serialize each surviving `general_table` (pipe-delimited, same format as the old `ingest/chunker.py::_serialize_table`) to `data/parsed/tables_sm.jsonl`: `{"section_title": title, "table_title": <caption or "">, "rows_text": serialized}`.
5. Take the tree's remaining text (`.get_text(separator=" ", strip=True)`) as clean prose. Emit to `data/parsed/pages_sm.jsonl`: `{"section_title": title, "text": cleaned_text}`. Skip rows with empty cleaned text (pure-table/pure-figure sections).

**Checkpoint:** `pages_sm.jsonl` row count ≈ `freeWord` row count (2,089, minus rows that clean to empty). `tables_sm.jsonl` contains the ~392 general/lookup tables, with zero register bit-tables leaking in. Spot-check that `IELSRn`'s prose no longer contains bit-table numbers, and `1.2. Block Diagram`'s prose no longer contains SVG label soup.

---

## Task 4 — Figure Discovery Index ⬜

**Actions:**
Implement `ingest/parser_smart_manual_figures.py`:
1. Scan `display_data` HTML in `freeWord`, `registerList`, and `bitList` for `<figure>` blocks
2. For each, extract the `<figcaption>` text and a locator back to its source row (table + row key) — do **not** extract or save the `<svg>` itself; it stays in the DB and is read live by `get_figure` (Task 6)
3. Emit one figure chunk per figure: `render_text = "[{section_title} > {figure_id}] {caption}"`

**Checkpoint:** Each figure chunk has a non-empty caption and a resolvable row locator.

---

## Task 5 — Chunking + Embedding ⬜

**Actions:**
- `ingest/chunker.py` — three chunk types now: `prose` (`RecursiveCharacterTextSplitter(chunk_size=500, chunk_overlap=80)` over `pages_sm.jsonl`), `table` (one chunk per `tables_sm.jsonl` record — the preserved general/lookup tables), `figure` (one per figure discovery record). Register bit-tables are intentionally excluded — `register_lookup` serves them live.
- `ingest/indexer.py` — reuse existing embedding + Chroma persistence, unchanged.

**Checkpoint:** `Chroma(...).similarity_search("clock generation circuit", k=3)` returns ≥ 1 relevant hit. `Chroma(...).similarity_search("function comparison pin count package", k=3)` surfaces the `1.4. Function Comparison` table chunk.

---

## Task 6 — Figure Tool (live query, no server) ⬜

**Actions:**
- `app/figure_tool.py` — `get_figure(figure_id, chip_part)`: Chroma lookup on the discovery index to find which row (`freeWord`/`registerList`/`bitList`) holds the figure, then a live query + BeautifulSoup re-parse of that row's `display_data` to extract the `<svg>`, returned directly as an `image/svg+xml` data URI in the tool response.
- No figure server needed — the DB, the MCP process, and the agent are all on the same local machine, so there's no reason to serve files over HTTP.

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
