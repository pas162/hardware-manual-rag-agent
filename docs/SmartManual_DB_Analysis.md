# Smart-Manual DB Analysis — `smart-manual-db/RA6M4_en`

*Investigated for reuse in: [POC_RAG_Hardware_UM_Plan.md](POC_RAG_Hardware_UM_Plan.md)*

---

## 1. File Identity

| Property | Value |
|---|---|
| Path | `smart-manual-db/RA6M4_en` |
| File type | SQLite 3 database (magic bytes: `53 51 4C 69 74 65 20 66 6F 72 6D 61 74 20 33`) |
| Size | 107 MB |
| DB version | `1.40.00` (from `databaseProperties`) |
| Register prefix | `R_` (FSP struct naming convention) |
| Chip | RA6M4 (English) |
| Source | Renesas "Smart Manual" toolchain — generated from original DITA/HTML source, **not** from the PDF |

---

## 2. Schema Overview

```
RA6M4_en (SQLite)
├── databaseProperties   (3 rows)    — DB metadata
├── moduleList           (112 rows)  — peripheral base addresses
├── registerList         (4,144 rows)— register definitions + HTML rendering
├── bitList              (20,457 rows)— bit-field definitions + HTML rendering
└── freeWord             (2,089 rows) — full UM section text (plain + HTML)
```

---

## 3. Table Details

### 3.1 `databaseProperties`

| field | value |
|---|---|
| `Version` | `1.40.00` |
| `Prefix` | `R_` |
| `CSS` | Embedded CSS stylesheet string for HTML rendering |

Metadata only. The `Prefix` value confirms FSP-aligned register naming throughout the DB.

---

### 3.2 `moduleList` — 112 rows

**Columns:**

| Column | Type | Notes |
|---|---|---|
| `module_symbol_name` | TEXT | FSP struct name, e.g. `R_ICU`, `R_SYSTEM`, `R_SCI0` |
| `module_base_address` | TEXT | Hex base address, e.g. `0x40006000` |
| `module_name` | TEXT | **NULL for all 112 rows** |
| `created_at` / `modified_at` | TEXT | **NULL for all rows** |

**Coverage:** All RA6M4 peripherals — DMAC0–7, DTC, ICU, SRAM, BUS, SCI0–9, SPI0–1, GPT0–9, IIC0–1, CAN0–1, ADC0–1, DAC, RTC, WDT, IWDT, AGT0–5, USBFS, SDHI, SSIE, OSPI, QSPI, ELC, CAC, CRC, DOC, ETHERC, EDMAC, TSN, CTSU, FACI, PSCU, CPSCU, PORT0–8, PFS, and more.

**Sample rows:**

| module_symbol_name | module_base_address |
|---|---|
| `R_RMPU` | `0x40000000` |
| `R_ICU` | `0x40006000` |
| `R_SYSTEM` | `0x4001E000` |
| `R_SCI0` | `0x40118000` |
| `R_GPT0` | `0x40169000` |
| `R_QSPI` | `0x64000000` |

> **Note:** Some peripherals appear twice under different alias names (e.g. `R_SYSC` and `R_SYSTEM` both map to `0x4001E000`; `R_IIC0` appears twice). Total distinct base addresses: ~100.

---

### 3.3 `registerList` — 4,144 rows

**Columns:**

| Column | Type | Notes |
|---|---|---|
| `register_symbol_name` | TEXT | FSP symbol, e.g. `IELSR0`, `SCKDIVCR`, `IRQCR0` |
| `register_name` | TEXT | Full English name, e.g. `"ICU Event Link Setting Register 0"` |
| `address` | TEXT | Absolute hex address (4,136 valid; 8 are `"---"` for array registers) |
| `mode` | TEXT | `NULL` for most; `"CALENDAR COUNT"` / `"BINARY COUNT"` for RTC variants |
| `module_symbol_name` | TEXT | FK → `moduleList.module_symbol_name` |
| `module_name` | TEXT | NULL for all rows |
| `display_data` | TEXT | **Full HTML page** — bit-position diagram + description |
| `created_at` / `modified_at` | TEXT | NULL for all rows |

**Address format distribution:**

| Format | Count |
|---|---|
| Valid hex (`0x...`) | 4,136 |
| `"---"` (array registers, e.g. `VBTBKR[*]`, `MKR[*]`, `MCTL_RX[*]`) | 8 |
| NULL | 0 |

**Top modules by register count:**

| Module | Count |
|---|---|
| `R_CAN0` / `R_CAN1` | 383 each |
| `R_PMISC` / `R_PFS` | 342 each |
| `R_ICU` | 131 |
| `R_BUS` | 83 |
| `R_ADC120` / `R_ADC0` | 81 each |
| `R_SYSTEM` / `R_SYSC` | 77 each |
| `R_RTC` | 75 |

**Key observations:**
- Registers are **individually addressed** — e.g. `IELSR0` through `IELSR95` each have their own row with their own absolute address, unlike the POC which stores `IELSRn` as a single prefix-matched entry.
- `SCKCR` (the POC's PDF-derived name) **does not exist** — the correct FSP name is `SCKDIVCR`. This is a known naming discrepancy between PDF-parsed and source-derived data.
- The `display_data` HTML contains the full bit-position diagram (base address, offset, bit field layout, reset values) and functional description — parseable with BeautifulSoup.

**Sample `display_data` structure (IELSR0):**
```html
<main role="main"><article ...>
  <h1 class="topictitle1">IELSRn : ICU Event Link Setting Register n (n = 0 to 95)</h1>
  <div class="body">
    <section class="section">
      <table class="frame-none" ...>
        <!-- Base address: ICU = 0x4000_6000 -->
        <!-- Offset address: 0x300 + 0x4 × n -->
        <!-- Bit position row: 31 30 29 ... 0 -->
        <!-- Bit field row: — — — DTCE — — — IR / IELS[8:0] -->
        <!-- Value after reset row: 0 0 0 ... 0 -->
      </table>
    </section>
    <!-- Functional description paragraphs -->
  </div>
</article></main>
```

---

### 3.4 `bitList` — 20,457 rows

**Columns:**

| Column | Type | Notes |
|---|---|---|
| `bit_name` | TEXT | Human-readable name, e.g. `"IRQi Detection Sense Select"` |
| `bit` | TEXT | Bit range string, e.g. `"1:0"`, `"7"`, `"31:25"` |
| `bit_symbol_name` | TEXT | Symbol, e.g. `IRQMD`, `IELS`, `DTCE`, `IR` |
| `address` | TEXT | Absolute address of the parent register |
| `register_symbol_name` | TEXT | FK → `registerList.register_symbol_name` |
| `module_symbol_name` | TEXT | FK → `moduleList.module_symbol_name` |
| `rwflg` | TEXT | **NULL for all 20,457 rows** |
| `function_text` | TEXT | **NULL for all 20,457 rows** |
| `display_data` | TEXT | **Full HTML table** with bit function + enumerated values |
| `mode` | TEXT | NULL for most; `"CALENDAR COUNT"` / `"BINARY COUNT"` for RTC |
| `created_at` / `modified_at` | TEXT | NULL for all rows |

**Key observations:**
- `rwflg` (R/W access) and `function_text` are **completely empty** across all rows — the R/W type and functional description are **only available by parsing `display_data` HTML**.
- The `display_data` is a `<table class="frame-all">` with columns: **Bit | Symbol | Function | R/W** — it contains full enumerated values (e.g. `"0 0: Falling edge"`, `"0 1: Rising edge"`, `"1 0: Rising and falling edges"`), which the POC's `bit_fields` table does not store at all.
- Some entries use wildcard names like `IRQCR[*]`, `DELSR[*]` — template rows for indexed register families.
- Distinct registers covered: **1,591** (vs. 511 in POC's `registers.db`).

**Sample `display_data` structure (IRQCR0 / IRQMD bit):**
```html
<table class="frame-all">
  <thead>
    <tr><th>Bit</th><th>Symbol</th><th colspan="2">Function</th><th>R/W</th></tr>
  </thead>
  <tbody>
    <tr>
      <td rowspan="5">1:0</td>
      <td rowspan="5">IRQMD[1:0]</td>
      <td colspan="2">IRQi Detection Sense Select</td>
      <td rowspan="5">R/W</td>
    </tr>
    <tr><td>0 0:</td><td>Falling edge</td></tr>
    <tr><td>0 1:</td><td>Rising edge</td></tr>
    <tr><td>1 0:</td><td>Rising and falling edges</td></tr>
    <tr><td>1 1:</td><td>Low level</td></tr>
  </tbody>
</table>
<commondisplaydata>
  <p>IRQCRi register changes must satisfy the following conditions: ...</p>
</commondisplaydata>
```

---

### 3.5 `freeWord` — 2,089 rows ⭐ Most Valuable for RAG

**Columns:**

| Column | Type | Notes |
|---|---|---|
| `unique_key` | TEXT | HTML filename key, e.g. `sls1570960141161.html` |
| `title` | TEXT | Section title with dotted numbering, e.g. `"13.2.15. IELSRn : ICU Event Link Setting Register n (n = 0 to 95)"` |
| `summary` | TEXT | Same as `title` (redundant) |
| `keyword` | TEXT | **Clean plain-text extraction of the full section** — no HTML tags |
| `display_data` | TEXT | **Full HTML rendering** of the section |
| `chapter` / `section` / `subsection` / `division` / `article` / `paragraph` | TEXT | **All NULL** |
| `page` | TEXT | **NULL for all rows** |
| `created_at` / `modified_at` | TEXT | NULL for all rows |

**Title hierarchy depth distribution:**

| Depth | Count | Example |
|---|---|---|
| 0 (no number) | 5 | — |
| 1 (chapter) | 50 | `"8. Clock Generation Circuit"` |
| 2 (section) | 337 | `"8.2. Register Descriptions"` |
| 3 (subsection) | 1,404 | `"8.2.2. SCKDIVCR : System Clock Division Control Register"` |
| 4 (sub-subsection) | 292 | `"2.7.4.1. DBGSTR : Debug Status Register"` |
| 5 (deepest) | 1 | `"14.8.3.5.1. Cache RAM Check"` |

**Top-level chapters covered (50 total):**

`1. Overview` · `2. CPU` · `3. Operating Modes` · `4. Address Space` · `5. Resets` · `6. Option-Setting Memory` · `7. LVD` · `8. Clock Generation Circuit` · `10. Low Power Modes` · `12. Register Write Protection` · `13. ICU` · `15. MPU` · `16. DMAC` · `17. DTC` · `18. ELC` · `20. POEG` · `21. GPT` · `22. AGT` · `23. RTC` · `24. WDT` · `25. IWDT` · `26. ETHERC` · `27. EDMAC` · `28. USBFS` · `29. SCI` · `30. IIC` · `31. CAN` · `32. SPI` · `34. OSPI` · `35. SSIE` · `36. SDHI` · `37. CRC` · `38. Boundary Scan` · `39. SCE9` · `40. ADC12` · `41. DAC12` · `42. TSN` · `43. CTSU` · `46. Standby SRAM` · `47. Flash Memory` · `49. Security Features`

**Register-description entries:** 715 entries have the pattern `"SYMBOL : Full Register Name"` in the title — each contains the full bit-field table as plain text in `keyword`.

**Key observations:**
- ~~The `keyword` field is immediately usable as RAG chunk text — clean prose, no HTML parsing needed.~~ **Correction (re-verified against the live RA6M4 DB during implementation):** `keyword` is a *flattened text soup*, not clean prose — for register sections it inlines the bit-position table's numbers and the `Bit | Symbol | Function | R/W` table with no separators (see sample below), and for sections containing a figure/diagram it inlines every text label extracted from inside the `<svg>` (e.g. `1.2. Block Diagram`'s `keyword` reads `...Memory Memory 1 MB code flash 1 MB code flash 8 KB data flash...`, every component label in that diagram, flattened into the sentence stream). Using `keyword` as-is for RAG chunking would pollute `search_um` with table/diagram-label noise. The ingestion pipeline instead parses `freeWord.display_data` (HTML), decomposes register `<table>`s and `<figure>`s, and takes the remaining text as prose — see `POC_RAG_Tasks.md` Task 3.
- The `keyword` for register sections (e.g. `IELSRn`) contains: base address, offset formula, bit layout, reset values, R/W per bit, enumerated values, and full functional description — **richer than the POC's `register_row` chunks** (this part of the original observation still holds — it's just why `keyword` can't be used verbatim as *prose*, since this richness is table data now served instead by `register_lookup`).
- **No page numbers anywhere** — `page` is NULL for all 2,089 rows. This is the primary gap vs. PDF-derived data.
- The `title` dotted numbering can be converted to a `section_path` string (e.g. `"13.2.15."` → `"§13. ICU > §13.2 Register Descriptions > §13.2.15 IELSRn"`).
- Of the 2,089 `freeWord` rows, 1,020 contain at least one `<table>` in `display_data`; **392 of those are not register-titled sections** (e.g. `1.4. Function Comparison`, `1.5. Pin Functions`, `2.7.2. Peripheral Address Map`) — these are general/lookup tables with no equivalent in `register_lookup` and must be preserved as their own chunk type, not discarded alongside register bit-tables.

**Sample `keyword` for `IELSRn` (truncated):**
```
IELSRn : ICU Event Link Setting Register n (n = 0 to 95)
Base address: ICU = 0x4000_6000  Offset address: 0x300 + 0x4 × n
Bit position: 31 30 ... 24 | 23 ... 17 | 16 | 15 ... 9 | 8:0
Bit field:     —  —  ... DTCE | — ... — | IR | — ... — | IELS[8:0]
Value after reset: 0 0 ... 0
Bit  Symbol    Function                          R/W
8:0  IELS[8:0] ICU Event Link Select             R/W
               0x00: Disable interrupts ...
               Others: Event signal number ...
16   IR         Interrupt Status Flag             R/W1
24   DTCE       DTC Activation Enable             R/W
...
[Full functional description paragraphs follow]
```

---

## 4. Comparison with POC `registers.db`

| Dimension | POC `registers.db` (PDF-parsed) | Smart-Manual DB |
|---|---|---|
| **Source** | PDF → pdfplumber table detection | DITA/HTML source (authoritative) |
| **Registers** | 511 | 4,144 (~8×) |
| **Bit fields** | 3,303 | 20,457 (~6×) |
| **R/W access per bit** | ✅ stored in `bit_fields.access` | ❌ only in `display_data` HTML |
| **Reset value per bit** | ✅ stored in `bit_fields.reset` | ❌ only in `display_data` HTML |
| **Bit enumerated values** | ❌ not stored | ✅ in `bitList.display_data` HTML |
| **Register addresses** | ✅ absolute hex | ✅ absolute hex (4,136 / 4,144) |
| **Module base addresses** | ❌ not stored | ✅ `moduleList` (112 peripherals) |
| **Section / page provenance** | ✅ `section_path` + `page_start` | ❌ no page numbers anywhere |
| **Prose text for RAG** | ✅ `chunks.jsonl` (prose chunks) | ✅ `freeWord.display_data` (HTML, parsed — `keyword` is unusable soup) |
| **Register naming convention** | PDF-derived (e.g. `SCKCR`) | FSP-aligned (e.g. `SCKDIVCR`) |
| **Indexed register variants** | Prefix match (`IELSRn` → `IELSR0`–`95`) | Individual rows per variant |
| **Figure images** | ✅ PNG + base64 + caption | ✅ native `<svg>` in `display_data` |
| **HTML rendering** | ❌ | ✅ all three data tables have `display_data` |

---

## 5. Known Issues & Gaps

| Issue | Detail |
|---|---|
| **No page numbers** | `freeWord.page` is NULL for all rows — citations cannot include `p.NNN` without cross-referencing the PDF |
| **`rwflg` / `function_text` empty** | R/W and function description for bits require HTML parsing of `bitList.display_data` |
| **Duplicate module aliases** | e.g. `R_SYSC` and `R_SYSTEM` both at `0x4001E000`; `R_IIC0` appears twice — deduplication needed on import |
| **Naming discrepancy** | PDF-derived names (e.g. `SCKCR`) differ from FSP names (e.g. `SCKDIVCR`) — `rapidfuzz` in `app/register_tool.py` mitigates this but `eval/golden_set_v2.csv` should be reviewed |
| **Array register addresses** | 8 registers use `"---"` as address (e.g. `VBTBKR[*]`, `MKR[*]`) — need special handling |
| **`module_name` always NULL** | Human-readable peripheral names not available from `moduleList` — must be derived from `module_symbol_name` by stripping `R_` prefix |
| **`chapter`/`section` hierarchy NULL** | `freeWord` section hierarchy fields are all NULL — section path must be reconstructed from the dotted `title` numbering |

---

## 6. Reuse Opportunities

### 🟢 High Value — Direct Reuse

#### A. Serve `register_lookup` live from `registerList` + `bitList`
No import step and no `registers.db` — `app/register_tool.py` queries the Smart Manual DB directly at request time:
1. Query `registerList` for exact + prefix matches (`LIKE 'name%'`) on `register_symbol_name` — enrich with the peripheral base address from `moduleList`.
2. Join `bitList` on `register_symbol_name`; parse each row's `display_data` HTML with BeautifulSoup to extract `bit`, `symbol`, `R/W`, `reset`, and enumerated values (not present as plain columns).
3. Deduplicate module aliases (e.g. `R_SYSC`/`R_SYSTEM`) in the live-query path, since there is no import script to do it.

This gives `register_lookup` the full 4,144-register coverage while keeping the tool contract unchanged and avoiding a duplicated second store.

#### B. Parse `freeWord.display_data` as the prose + general-table corpus for `search_um`
Not `keyword` (see correction above) — `display_data` (HTML) is parsed with BeautifulSoup per section: register `<table>`s and `<figure>` blocks are decomposed out first, remaining `<table>`s are kept as general/lookup-table chunks, and the leftover text becomes the prose chunk. The dotted `title` numbering maps to `section_path`.

#### C. Use `moduleList` as a peripheral registry
Enrich `register_lookup` responses with `base_address` and enable a potential new `list_peripherals(chip_part)` tool.

### 🟡 Medium Value — Requires HTML Parsing

#### D. Parse `bitList.display_data` for enumerated bit values
BeautifulSoup extraction of the `<table class="frame-all">` rows gives enumerated values per bit setting — not currently stored in the POC at all.

#### E. Parse `registerList.display_data` for reset values
The bit-position diagram HTML contains per-bit reset values, useful for validating or enriching the `bit_fields.reset` column.

### 🔴 Not Reusable

#### F. Page numbers
All `page` fields are NULL — the PDF remains the only source for page-level citations.

#### G. Figure images — native `<svg>` in `display_data`
Figures are embedded as native `<svg>` markup inside the `display_data` HTML across `freeWord`, `registerList`, and `bitList`. Only the `<figcaption>` needs pre-indexing for discovery; the `<svg>` itself is read live from the DB and parsed with BeautifulSoup by `get_figure` on each call — no PNG extraction, no `.svg` files on disk, no HTTP server. `ingest/parser_figures.py` is no longer required; the discovery index built by `ingest/parser_smart_manual_figures.py` stores only `{figure_id, caption, section_title}`.
pipeline remain necessary for `get_figure`.

---

## 7. Recommended Integration Path

```
smart-manual-db/RA6M4_en (SQLite, read directly — no import)
│
├─▶ registerList + bitList ──▶ app/register_tool.py
│ live SQLite query + BeautifulSoup parse of display_data
│ (address, R/W, reset, enumerated values) → RegisterRecord
│
├─▶ <figure> captions in display_data ──▶ ingest/parser_smart_manual_figures.py
│ discovery index only {figure_id, caption, section_title}
│ the <svg> stays in the DB, read live by app/figure_tool.py
│
└─▶ freeWord.display_data ──▶ ingest/parser_smart_manual_text.py
│ classify each <table> (register vs. general)
└─▶ data/parsed/pages_sm.jsonl (prose) + tables_sm.jsonl (general tables)
└─▶ ingest/chunker.py (rewrite — 3 chunk types)
└─▶ data/parsed/chunks.jsonl
└─▶ ingest/indexer.py (unchanged)
└─▶ data/store/chroma/
```

**Registers, bit-fields, and figure SVGs are queried live** — only prose, general/lookup tables, and figure captions are pre-embedded into ChromaDB. There is no `registers.db` and no import script.

**One normalization step needed:** register names follow the Smart Manual's FSP convention (e.g. `SCKDIVCR`, not the PDF's `SCKCR`). `rapidfuzz` in `app/register_tool.py` maps PDF-era names as a convenience layer, and `eval/golden_set_v2.csv` should be updated to use FSP names directly.
