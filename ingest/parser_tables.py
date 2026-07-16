"""
Detect register tables in a PDF UM using pdfplumber.

A table is classified as a register table when its header row matches >= 3 of:
  {Bit, Bit Name, Symbol, Value, R/W, Reset, Description}  (case-insensitive)

Output: data/parsed/tables.jsonl — one JSON object per detected register table.
"""

import json
import re
from pathlib import Path

import pdfplumber

REGISTER_HEADER_KEYWORDS = {"symbol", "value", "r/w", "reset", "description", "function"}
# A register table MUST have a "Bit" column, plus at least one other register keyword.
_RE_BIT_CELL = re.compile(r"^bit\b", re.IGNORECASE)


def _is_register_table(rows: list[list]) -> bool:
    if not rows:
        return False
    # The actual header row may be row[0] or row[1] (some tables have a merged title row first)
    for candidate_row in rows[:2]:
        if candidate_row is None:
            continue
        header_cells = [str(c).strip() for c in candidate_row if c]
        header_lower = {c.lower() for c in header_cells}
        # Must have a "Bit" or "Bit Name" column — eliminates all non-register tables
        has_bit = any(_RE_BIT_CELL.match(c) for c in header_cells)
        if not has_bit:
            continue
        # Plus at least one other register-specific column
        other_matches = sum(1 for kw in REGISTER_HEADER_KEYWORDS if any(kw in cell for cell in header_lower))
        if other_matches >= 1:
            return True
    return False


# A cell "looks like data" when it contains a hex/address pattern or a
# number followed by a unit — patterns that never appear in header labels.
_RE_DATA_LIKE = re.compile(r"0x[0-9A-Fa-f_]+|\b\d+\s*(KB|MB|GB|bit|Hz|MHz)\b", re.IGNORECASE)


def _first_data_row_idx(rows: list[list], start: int, search_limit: int) -> int:
    """Return the index of the first row that looks like real data.

    A data row has at least one data-shaped cell (hex address, sized unit) and
    is not mostly empty. Rows before it (from *start*) are header levels —
    covers both single-row headers and multi-level headers with parent/child
    label rows, since header cells never match the data-shaped pattern.
    """
    for i in range(start, min(search_limit, len(rows))):
        row = rows[i]
        if row is None:
            continue
        if any(c and _RE_DATA_LIKE.search(str(c)) for c in row):
            return i
    # No data-shaped cell found in range — assume exactly one header row.
    return start + 1


def _forward_fill_row(prev: list, row: list) -> list:
    """Fill None cells in *row* from the same column in *prev* (merged-cell continuation)."""
    filled = list(row)
    for i in range(len(filled)):
        if filled[i] is None and i < len(prev):
            filled[i] = prev[i]
    return filled


_RE_BULLET_LINE = re.compile(r"^\s*(?:[•·-]|\d+\s*[.):]|\d[\s\d]*:)\s*")


def _clean_cell_text(text: str) -> str:
    """Join multi-line bullet-style cell content into a single '; '-separated string.

    pdfplumber preserves each visual line inside a cell as '\\n' — option lists
    like "0 0 0:\\nSunday\\n0 0 1: Monday" read poorly as embedded prose. Blank
    lines and pure continuation wraps (no bullet marker) are joined without a
    new separator so wrapped sentences stay intact.
    """
    lines = [ln.strip() for ln in text.split("\n") if ln.strip()]
    if len(lines) <= 1:
        return text.replace("\n", " ").strip()
    parts: list[str] = []
    for ln in lines:
        if _RE_BULLET_LINE.match(ln) or not parts:
            parts.append(ln)
        else:
            parts[-1] = f"{parts[-1]} {ln}"
    return "; ".join(parts)


def _flatten_header_levels(header_rows: list[list]) -> list[str]:
    """Combine 1+ header rows into flat 'Parent - Child' column names.

    Each header row is forward-filled left-to-right first (merged parent cells
    span multiple child columns), then levels are joined per-column with ' - ',
    skipping empty/duplicate levels.
    """
    n_cols = max(len(r) for r in header_rows)
    filled_levels = []
    for row in header_rows:
        padded = list(row) + [None] * (n_cols - len(row))
        # Forward-fill within the row (merged header cell spans multiple columns)
        filled = []
        last = ""
        for c in padded:
            text = str(c).strip() if c else ""
            if text:
                last = text
            filled.append(last)
        filled_levels.append(filled)

    header = []
    for col in range(n_cols):
        levels = []
        for level in filled_levels:
            text = level[col]
            if text and (not levels or levels[-1] != text):
                levels.append(text)
        header.append(" - ".join(levels))
    return header


def _rows_to_dicts(rows: list[list]) -> tuple[list[str], list[dict]]:
    """Return (header, data_rows). Skips title rows like 'Table X.Y ...'."""
    if len(rows) < 1:
        return [], []

    # For register tables, prefer the row that has "Bit" + other keywords
    header_idx = 0
    register_found = False
    for i, row in enumerate(rows[:3]):
        if row is None:
            continue
        cells = [str(c).strip() for c in row if c]
        cells_lower = {c.lower() for c in cells}
        has_bit = any(_RE_BIT_CELL.match(c) for c in cells)
        other = sum(1 for kw in REGISTER_HEADER_KEYWORDS if any(kw in cell for cell in cells_lower))
        if has_bit and other >= 1:
            header_idx = i
            register_found = True
            break

    if register_found:
        header = [str(c).strip() if c else "" for c in rows[header_idx]]
        data_start = header_idx + 1
    else:
        # For general tables: skip the title row, then find where the header
        # row(s) end and real data starts, using content shape rather than
        # cell-count (handles both single-row and multi-level headers).
        start = 0
        for i, row in enumerate(rows[:3]):
            if row is None:
                continue
            cells = [str(c).strip() for c in row if c]
            if cells and _RE_TABLE_TITLE.match(cells[0]):
                start = i + 1
                break

        header_end = _first_data_row_idx(rows, start, search_limit=start + 4)

        header_rows = [r for r in rows[start:header_end] if r is not None]
        if not header_rows:
            header_rows = [rows[start]] if start < len(rows) else [[]]

        header = _flatten_header_levels(header_rows) if len(header_rows) > 1 else \
            [str(c).strip() if c else "" for c in header_rows[0]]
        data_start = header_end

    result = []
    prev_row: list | None = None
    for row in rows[data_start:]:
        if row is None:
            continue
        filled = _forward_fill_row(prev_row, row) if prev_row is not None else list(row)
        prev_row = filled
        if register_found:
            cells = [str(c).strip() if c else "" for c in filled]
        else:
            cells = [_clean_cell_text(str(c)) if c else "" for c in filled]
        d = dict(zip(header, cells))
        # Normalise "Function" column → also store as "description"
        if "Function" in d and "Description" not in d:
            d["Description"] = d["Function"]
        result.append(d)
    return header, result


_RE_TABLE_TITLE = re.compile(r"^Table\s+\d+\.\d+", re.IGNORECASE)


def _find_header_and_title(rows: list[list]) -> tuple[int, str]:
    """Return (header_row_index, table_title).

    Handles two layouts:
      - rows[0] = ["Table X.Y Some Title", ""]  → title row, header is rows[1]
      - rows[0] = ["Feature", "Description"]    → no title, header is rows[0]
    """
    title = ""
    for i, row in enumerate(rows[:3]):
        if row is None:
            continue
        first_cell = str(row[0]).strip() if row else ""
        if _RE_TABLE_TITLE.match(first_cell):
            title = first_cell
            # Header is the next non-None row
            for j in range(i + 1, min(i + 3, len(rows))):
                if rows[j] is not None:
                    return j, title
        else:
            return i, title
    return 0, title


_RE_REG_NAME_HEADING = re.compile(
    r"\b([A-Z][A-Z0-9_]{2,}(?:n|m)?)\s*(?::|Register|Reg\.?)",
)
_RE_PERIPHERAL_HEADING = re.compile(
    r"§\s*\d+(?:\.\d+)*\s+([A-Z][A-Za-z0-9 _/\-]+?)(?:\s*>|\s*$)",
)


def _extract_register_name(section_path: str, page_blocks: list[dict]) -> tuple[str, str]:
    """Return (register_name, peripheral) from the section heading or nearby text."""
    reg_name = ""
    peripheral = "UNKNOWN"

    if section_path:
        parts = section_path.split(">")
        deepest = parts[-1].strip() if parts else ""

        # Register name: deepest segment matching "§X.Y REGNAME :" or "§X.Y REGNAME/"
        # Handles suffixes: VBTBKR[n], CSnREC, SARUy, PmnPFS, BUSSCNT<slave>
        m = re.search(r"§[\d.]+\s+([A-Z][A-Z0-9_/\[\]<>a-z]+)\s*[:/]", deepest)
        if m:
            reg_name = m.group(1).split("/")[0].strip()

        # Peripheral: top-level chapter title — most stable signal across any UM.
        # e.g. "§19. I/O Ports" → "I/O Ports", "§8. Clock Generation Circuit" → "Clock Generation Circuit"
        if len(parts) >= 1:
            top = parts[0].strip()
            pm = re.search(r"§[\d.]+\s+(.+)", top)
            if pm:
                peripheral = pm.group(1).strip()

    if reg_name:
        return reg_name, peripheral

    # Fall back to scanning text blocks for "REGNAME : ... Register"
    for block in page_blocks:
        text = block.get("text", "")
        m = _RE_REG_NAME_HEADING.search(text)
        if m:
            return m.group(1), peripheral

    # Return peripheral even when register name is not found
    return "", peripheral


def _build_figure_zones(
    figures_jsonl: Path, doc_id: str = ""
) -> dict[int, list[tuple[float, float]]]:
    """Return page -> list of (crop_y0, crop_y1) figure regions.

    Uses the precise crop coordinates saved by parser_figures so we can
    detect exact overlap between a pdfplumber table bbox and a figure region.
    Falls back to (caption_y - page_height*0.45, caption_y1) when crop coords
    are not available (old figures.jsonl without these fields).

    Only rows matching doc_id are considered when figures_jsonl holds rows
    from more than one document.
    """
    zones: dict[int, list[tuple[float, float]]] = {}
    if not figures_jsonl.exists():
        return zones
    with figures_jsonl.open(encoding="utf-8") as f:
        for line in f:
            fig = json.loads(line)
            if fig.get("doc_id") != doc_id:
                continue
            page = fig.get("page")
            if not page:
                continue
            crop_y0 = fig.get("crop_y0")
            crop_y1 = fig.get("crop_y1")
            if crop_y0 is not None and crop_y1 is not None and crop_y1 > crop_y0:
                zones.setdefault(page, []).append((float(crop_y0), float(crop_y1)))
            else:
                # Fallback: use caption_y as bottom of figure zone
                caption_y = fig.get("caption_y")
                if caption_y is not None:
                    zones.setdefault(page, []).append((0.0, float(caption_y) + 20))
    return zones


def _table_in_figure_zone(
    table_bbox: tuple[float, float, float, float],
    figure_zones: list[tuple[float, float]],
) -> bool:
    """Return True if the table bbox overlaps with any figure region.

    pdfplumber bbox: (x0, top, x1, bottom) in PDF points (top-down).
    Overlap means the table's vertical span intersects a figure's crop region.
    """
    _, top, _, bottom = table_bbox
    for fy0, fy1 in figure_zones:
        # Overlap: table top < figure bottom AND table bottom > figure top
        if top < fy1 and bottom > fy0:
            return True
    return False


def parse_tables(
    pdf_path: str | Path,
    output_path: str | Path,
    pages_jsonl: Path | None = None,
    figures_jsonl: Path | None = None,
    doc_id: str = "",
    mode: str = "w",
) -> int:
    """Detect tables and write JSONL to *output_path*.

    doc_id is stamped onto every record and used to filter pages_jsonl/
    figures_jsonl rows down to this document when those files hold rows from
    more than one document. mode="a" appends instead of overwriting — used by
    run_all.py for multi-doc runs.

    Returns number of tables written.
    """
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Build figure zone index to exclude table detections inside figures
    figure_zones = (
        _build_figure_zones(Path(figures_jsonl), doc_id) if figures_jsonl else {}
    )

    # Build page → blocks index if pages_jsonl provided (this document's rows only)
    page_blocks: dict[int, list[dict]] = {}
    if pages_jsonl and Path(pages_jsonl).exists():
        with Path(pages_jsonl).open(encoding="utf-8") as f:
            for line in f:
                b = json.loads(line)
                if b.get("doc_id") == doc_id:
                    page_blocks.setdefault(b["page"], []).append(b)

    count = 0
    with pdfplumber.open(str(pdf_path)) as pdf, output_path.open(mode, encoding="utf-8") as fout:
        total_pages = len(pdf.pages)
        for page_num, page in enumerate(pdf.pages):
            if page_num % 100 == 0:
                print(f"    page {page_num + 1}/{total_pages} ({page_num * 100 // total_pages}%)  tables so far: {count}", flush=True)
            tables = page.extract_tables(table_settings={"snap_tolerance": 3})
            table_objects = page.find_tables(table_settings={"snap_tolerance": 3})
            if not tables:
                continue
            pn = page_num + 1
            blocks = page_blocks.get(pn, [])
            section_path = blocks[0].get("section_path", "") if blocks else ""
            page_figure_ys = figure_zones.get(pn, [])

            # When pages.jsonl has no blocks for this page (figure-heavy or table-only
            # pages), build synthetic blocks from pdfplumber so _extract_register_name
            # can still find the section heading above the table.
            if not blocks:
                raw_words = page.extract_words()
                if raw_words:
                    # Reconstruct a single synthetic block from all words on the page
                    raw_text = page.extract_text() or ""
                    if raw_text.strip():
                        blocks = [{"text": raw_text, "bbox": [0, 0, page.width, page.height], "section_path": ""}]

            for table_idx, (rows, tobj) in enumerate(zip(tables, table_objects)):
                is_register = _is_register_table(rows)

                # Skip any table (register or not) that sits inside a figure zone
                if page_figure_ys and not is_register:
                    bbox = tobj.bbox  # (x0, top, x1, bottom) in pdfplumber coords
                    if _table_in_figure_zone(bbox, page_figure_ys):
                        continue

                if is_register:
                    reg_name, peripheral = _extract_register_name(section_path, blocks)
                else:
                    reg_name, peripheral = "", ""

                header, data_rows = _rows_to_dicts(rows)
                _, table_title = _find_header_and_title(rows)

                # Additional junk filters for non-register tables
                if not is_register:
                    if not data_rows and not table_title:
                        continue
                    if len(header) <= 1:
                        continue
                    if len(data_rows) < 2 and not table_title:
                        continue

                record = {
                    "doc_id": doc_id,
                    "page": pn,
                    "table_idx": table_idx,
                    "section_path": section_path,
                    "is_register": is_register,
                    "table_title": table_title,
                    "register_name": reg_name,
                    "peripheral": peripheral,
                    "header": header,
                    "rows": data_rows,
                }
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

    return count


if __name__ == "__main__":
    registry_path = Path("data/registry.json")
    registry = json.loads(registry_path.read_text())
    doc_info = registry[0]

    pdf_path = Path(doc_info["path"])
    output_path = Path("data/parsed/tables.jsonl")

    print(f"Scanning {pdf_path} for register tables ...")
    n = parse_tables(
        pdf_path,
        output_path,
        pages_jsonl=Path("data/parsed/pages.jsonl"),
        figures_jsonl=Path("data/parsed/figures.jsonl"),
        doc_id=doc_info["doc_id"],
    )
    print(f"Found {n} register tables → {output_path}")

    # Checkpoint: spot-check known registers
    known = {"sckcr", "ielsr", "pcntr1"}
    found_known: dict[str, list[int]] = {k: [] for k in known}

    with output_path.open(encoding="utf-8") as f:
        for line in f:
            t = json.loads(line)
            header_str = " ".join(t.get("header", [])).lower()
            # Also check surrounding text isn't available — check row content
            rows_text = json.dumps(t.get("rows", [])).lower()
            for kw in known:
                if kw in header_str or kw in rows_text:
                    found_known[kw].append(t["page"])

    print(f"\nCheckpoint: {n} register tables (target >= 50)")
    for kw, pages in found_known.items():
        status = "FOUND" if pages else "NOT FOUND"
        print(f"  {kw.upper()}: {status} on pages {pages[:5]}")
