"""
Parse freeWord.display_data (HTML) into clean prose + preserved general tables.

freeWord.keyword is NOT used — it flattens register bit-table numbers and SVG
figure-label text into the prose with no separators (verified against the live
RA6M4 DB; see docs/SmartManual_DB_Analysis.md). Instead, display_data (HTML) is
parsed per section:

  - register tables (Bit|Symbol|Function|R/W header, or the borderless
    frame-none bit-position diagram) are discarded — register_lookup serves
    them live with richer structure.
  - all other tables ("general tables" — Function Comparison, Pin Lists,
    Address Maps, ...) are preserved as their own record in tables_sm.jsonl.
  - <figure> blocks are removed (handled separately by
    parser_smart_manual_figures.py).
  - the remaining text becomes the prose record in pages_sm.jsonl.

Output:
  data/parsed/pages_sm.jsonl   {"section_title": str, "text": str}
  data/parsed/tables_sm.jsonl  {"section_title": str, "table_title": str, "rows_text": str}
"""

import json
import re
import sqlite3
from pathlib import Path

from bs4 import BeautifulSoup

from app.smart_manual_locator import locate

_RE_BIT_HEADER = re.compile(r"^bit\b", re.IGNORECASE)


def _is_register_table(table) -> bool:
    """A register bit-table has a Bit|Symbol|Function|R/W header, or is the
    borderless bit-position diagram (frame-none, no <th> at all)."""
    ths = [th.get_text(strip=True) for th in table.find_all("th")]
    if ths and _RE_BIT_HEADER.match(ths[0]):
        return True
    classes = table.get("class") or []
    if "frame-none" in classes and not ths:
        return True
    return False


def _serialize_table(table) -> str:
    """Serialize a <table> to pipe-delimited text, one row per line."""
    lines = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(separator=" ", strip=True) for c in tr.find_all(["th", "td"])]
        cells = [c for c in cells if c]
        if cells:
            lines.append(" | ".join(cells))
    return "\n".join(lines)


def _table_caption(table) -> str:
    caption = table.find("caption")
    return caption.get_text(strip=True) if caption else ""


def parse_freewords(db_path: Path, pages_out: Path, tables_out: Path) -> tuple[int, int]:
    """Parse every freeWord row. Returns (prose_count, table_count)."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.execute("SELECT title, display_data FROM freeWord")

    pages_out.parent.mkdir(parents=True, exist_ok=True)
    tables_out.parent.mkdir(parents=True, exist_ok=True)

    prose_count = 0
    table_count = 0

    with pages_out.open("w", encoding="utf-8") as pages_f, \
         tables_out.open("w", encoding="utf-8") as tables_f:

        for row in cur.fetchall():
            title = row["title"]
            display_data = row["display_data"] or ""
            if not display_data.strip():
                continue

            soup = BeautifulSoup(display_data, "html.parser")

            for figure in soup.find_all("figure"):
                figure.decompose()

            for table in soup.find_all("table"):
                if table.attrs is None:
                    continue  # already decomposed as a nested descendant
                if table.find_parent("table") is not None:
                    continue  # nested table; handled as part of its ancestor
                if _is_register_table(table):
                    table.decompose()
                    continue

                rows_text = _serialize_table(table)
                if rows_text.strip():
                    tables_f.write(json.dumps({
                        "section_title": title,
                        "table_title": _table_caption(table),
                        "rows_text": rows_text,
                    }, ensure_ascii=False) + "\n")
                    table_count += 1
                table.decompose()

            text = soup.get_text(separator=" ", strip=True)
            if text:
                pages_f.write(json.dumps({
                    "section_title": title,
                    "text": text,
                }, ensure_ascii=False) + "\n")
                prose_count += 1

    con.close()
    return prose_count, table_count


if __name__ == "__main__":
    import sys

    chip = sys.argv[1] if len(sys.argv) > 1 else "RA6M4"
    db_path = locate(chip)
    pages_out = Path("data/parsed/pages_sm.jsonl")
    tables_out = Path("data/parsed/tables_sm.jsonl")

    prose_count, table_count = parse_freewords(db_path, pages_out, tables_out)
    print(f"prose rows: {prose_count}")
    print(f"general table rows: {table_count}")
