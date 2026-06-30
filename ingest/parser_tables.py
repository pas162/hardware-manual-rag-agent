"""
Detect tables in a PDF UM using camelot (lattice mode).

camelot lattice handles merged-cell headers correctly, producing clean
column labels like "IELSRn > Connect to NVIC" instead of pdfplumber's
flattened first-row output.

Output: data/parsed/tables.jsonl — one JSON object per detected table.
"""

import json
import re
from pathlib import Path

import camelot
import fitz  # PyMuPDF — used only to get page height for bbox conversion

REQUIRED_REGISTER_COLUMNS = {"bit", "symbol", "function", "r/w"}
_RE_BIT_CELL = re.compile(r"^bit\b", re.IGNORECASE)
_RE_TABLE_TITLE = re.compile(r"^Table\s+\d+\.\d+", re.IGNORECASE)
_RE_REG_NAME_HEADING = re.compile(r"\b([A-Z][A-Z0-9_]{2,}(?:n|m)?)\s*(?::|Register|Reg\.?)")
_RE_PERIPHERAL_HEADING = re.compile(r"§\s*\d+(?:\.\d+)*\s+([A-Z][A-Za-z0-9 _/\-]+?)(?:\s*>|\s*$)")


# ---------------------------------------------------------------------------
# Header building (handles single-row and multi-row merged-cell headers)
# ---------------------------------------------------------------------------

def _looks_like_data_row(cells: list[str]) -> bool:
    """Return True if a row contains data values rather than header labels."""
    indicators = 0
    for c in cells:
        c = c.strip()
        if c.startswith("0x") or c.startswith("0X"):
            indicators += 1
        if c in ("✓", "—", "×", "●", "○"):
            indicators += 1
    return indicators >= 2


def _detect_header_end(df) -> int:
    """Return the index of the first data row (everything before is header)."""
    for i in range(min(4, len(df))):
        if _looks_like_data_row([str(df.iloc[i, c]).strip() for c in range(len(df.columns))]):
            return i
    return 1


def _build_header(df, header_end: int) -> list[str]:
    """
    Build clean column labels from a multi-row header block.

    Strategy:
    1. Forward-fill each header row (merged cell → repeat label rightward).
    2. Use original blank info to distinguish sub-labels from carry-over:
       only include a row's value if the original cell was non-blank,
       OR if it's the first (group) row.
    3. Combine group + sub as "Group > Sub"; standalone columns use label only.
    """
    n_cols = len(df.columns)

    # Build filled and original rows
    filled_rows: list[list[str]] = []
    orig_rows: list[list[str]] = []
    for row_idx in range(header_end):
        orig = [str(df.iloc[row_idx, c]).strip() for c in range(n_cols)]
        orig_rows.append(orig)
        last = ""
        filled = []
        for cell in orig:
            if cell:
                last = cell
            filled.append(last)
        filled_rows.append(filled)

    if not filled_rows:
        return [f"col{c}" for c in range(n_cols)]

    result = []
    for col in range(n_cols):
        parts: list[str] = []
        for row_idx, (filled_row, orig_row) in enumerate(zip(filled_rows, orig_rows)):
            label = filled_row[col] if (orig_row[col] or row_idx == 0) else ""
            label = re.sub(r"\s*\n\s*", " ", label).strip()
            if label and label not in parts:
                parts.append(label)
        if not parts:
            result.append(f"col{col}")
        elif len(parts) == 1:
            result.append(parts[0])
        else:
            result.append(" > ".join(parts))

    return result


# ---------------------------------------------------------------------------
# Register table detection
# ---------------------------------------------------------------------------

def _is_register_header(header: list[str]) -> bool:
    """Return True only for canonical register bit-field tables.

    Expected logical columns in the RA6M4 UM:
      Bit | Symbol | Function | R/W
    """
    cells_lower = {c.lower().strip() for c in header if c}
    return REQUIRED_REGISTER_COLUMNS.issubset(cells_lower)


# ---------------------------------------------------------------------------
# Figure zone helpers (unchanged logic from original parser_tables.py)
# ---------------------------------------------------------------------------

def _build_figure_zones(figures_jsonl: Path) -> dict[int, list[tuple[float, float]]]:
    zones: dict[int, list[tuple[float, float]]] = {}
    if not figures_jsonl.exists():
        return zones
    with figures_jsonl.open(encoding="utf-8") as f:
        for line in f:
            fig = json.loads(line)
            page = fig.get("page")
            if not page:
                continue
            crop_y0 = fig.get("crop_y0")
            crop_y1 = fig.get("crop_y1")
            if crop_y0 is not None and crop_y1 is not None and crop_y1 > crop_y0:
                zones.setdefault(page, []).append((float(crop_y0), float(crop_y1)))
            else:
                caption_y = fig.get("caption_y")
                if caption_y is not None:
                    zones.setdefault(page, []).append((0.0, float(caption_y) + 20))
    return zones


def _table_in_figure_zone(
    bbox_top_origin: tuple[float, float, float, float],
    figure_zones: list[tuple[float, float]],
) -> bool:
    """bbox_top_origin: (x0, top, x1, bottom) with y=0 at top (pdfplumber convention)."""
    _, top, _, bottom = bbox_top_origin
    for fy0, fy1 in figure_zones:
        if top < fy1 and bottom > fy0:
            return True
    return False


# ---------------------------------------------------------------------------
# Register name / peripheral extraction (unchanged)
# ---------------------------------------------------------------------------

def _extract_register_name(section_path: str, page_blocks: list[dict]) -> tuple[str, str]:
    reg_name = ""
    peripheral = "UNKNOWN"

    if section_path:
        parts = section_path.split(">")
        deepest = parts[-1].strip() if parts else ""
        m = re.search(r"§[\d.]+\s+([A-Z][A-Z0-9_/\[\]<>a-z]+)\s*[:/]", deepest)
        if m:
            reg_name = m.group(1).split("/")[0].strip()
        if len(parts) >= 1:
            top = parts[0].strip()
            pm = re.search(r"§[\d.]+\s+(.+)", top)
            if pm:
                peripheral = pm.group(1).strip()

    if reg_name:
        return reg_name, peripheral

    for block in page_blocks:
        text = block.get("text", "")
        m = _RE_REG_NAME_HEADING.search(text)
        if m:
            return m.group(1), peripheral

    return "", peripheral


# ---------------------------------------------------------------------------
# Main parse function
# ---------------------------------------------------------------------------

def parse_tables(
    pdf_path: str | Path,
    output_path: str | Path,
    pages_jsonl: Path | None = None,
    figures_jsonl: Path | None = None,
) -> int:
    """Extract tables with camelot (lattice) and write JSONL to output_path.

    Returns number of tables written.
    """
    pdf_path = Path(pdf_path)
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    figure_zones = _build_figure_zones(Path(figures_jsonl)) if figures_jsonl else {}

    # Page → text blocks index (for register name extraction)
    page_blocks: dict[int, list[dict]] = {}
    if pages_jsonl and Path(pages_jsonl).exists():
        with Path(pages_jsonl).open(encoding="utf-8") as f:
            for line in f:
                b = json.loads(line)
                page_blocks.setdefault(b["page"], []).append(b)

    # Pre-load page heights for bbox conversion (camelot y=0 at bottom → flip)
    doc = fitz.open(str(pdf_path))
    page_heights = {i + 1: doc[i].rect.height for i in range(len(doc))}
    total_pages = len(doc)
    doc.close()

    count = 0
    with output_path.open("w", encoding="utf-8") as fout:
        for page_num in range(1, total_pages + 1):
            if page_num % 100 == 0:
                print(f"    page {page_num}/{total_pages} ({page_num * 100 // total_pages}%)  tables so far: {count}", flush=True)

            try:
                tables = camelot.read_pdf(str(pdf_path), pages=str(page_num), flavor="lattice")
            except Exception:
                continue

            if not tables:
                continue

            pn = page_num
            page_height = page_heights[pn]
            blocks = page_blocks.get(pn, [])
            section_path = blocks[0].get("section_path", "") if blocks else ""
            page_figure_zones = figure_zones.get(pn, [])

            for table_idx, table in enumerate(tables):
                df = table.df
                if df.empty:
                    continue

                # Convert camelot bbox (y=0 at bottom) → top-origin (y=0 at top)
                cx1, cy1, cx2, cy2 = table._bbox
                bbox = (cx1, page_height - cy2, cx2, page_height - cy1)

                # Detect header rows and build clean column labels
                header_end = _detect_header_end(df)
                header = _build_header(df, header_end)
                is_register = _is_register_header(header)

                # Skip tables inside figure zones (register tables are always kept)
                if page_figure_zones and not is_register:
                    if _table_in_figure_zone(bbox, page_figure_zones):
                        continue

                # Extract data rows
                data_rows = []
                for _, row in df.iloc[header_end:].iterrows():
                    cells = [str(c).strip() for c in row]
                    if any(cells):
                        d = dict(zip(header, cells))
                        data_rows.append(d)

                # Extract table title from first row if it looks like "Table X.Y ..."
                table_title = ""
                first_cell = str(df.iloc[0, 0]).strip()
                if _RE_TABLE_TITLE.match(first_cell):
                    table_title = first_cell

                # Junk filters for non-register tables
                if not is_register:
                    if not data_rows and not table_title:
                        continue
                    if len(data_rows) < 2 and not table_title:
                        continue

                reg_name = ""
                peripheral = ""
                if is_register:
                    reg_name, peripheral = _extract_register_name(section_path, blocks)

                rec = {
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
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                count += 1

    return count


if __name__ == "__main__":
    pdf_path = Path("data/pdfs/r01uh0890ej0160-ra6m4.pdf")
    output_path = Path("data/parsed/tables.jsonl")
    pages_jsonl = Path("data/parsed/pages.jsonl")
    figures_jsonl = Path("data/parsed/figures.jsonl")

    print(f"Scanning {pdf_path} for tables (camelot lattice) ...")
    n = parse_tables(
        pdf_path,
        output_path,
        pages_jsonl=pages_jsonl,
        figures_jsonl=figures_jsonl,
    )
    print(f"Found {n} tables -> {output_path}")

    # Checkpoint: spot-check known registers
    print(f"\nCheckpoint: {n} tables")