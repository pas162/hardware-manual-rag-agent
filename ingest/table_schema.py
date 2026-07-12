"""
Build the SQLite general_tables database from parsed non-register tables.

Reads:
  data/parsed/tables.jsonl  (from parser_tables.py)
  data/registry.json

Writes:
  data/store/registers.db  (general_tables table, same DB file as registers)

general_tables holds the full, faithful markdown for each non-register table so
get_table(table_id) can return exact data — vector search never has to
reconstruct a table, mirroring how register_lookup returns verbatim SQLite data
rather than trusting the register_row vector (see PROJECT_PLAN.md §4.4).
"""

import json
import re
import sqlite3
from pathlib import Path

CREATE_GENERAL_TABLES = """
CREATE TABLE IF NOT EXISTS general_tables (
    table_id      TEXT,
    title         TEXT,
    markdown      TEXT,
    doc_id        TEXT,
    chip_part     TEXT,
    section_path  TEXT,
    page          INTEGER,
    PRIMARY KEY (table_id, doc_id)
);
"""

_RE_BIT_HEADER = re.compile(r"^bit\b", re.IGNORECASE)
_RE_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def make_table_id(table_title: str, page: int, table_idx: int) -> str:
    """Derive a stable table_id from the title (or page/index if no title)."""
    if table_title:
        m = re.match(r"Table\s+(\d+\.\d+)", table_title, re.IGNORECASE)
        if m:
            return f"table-{m.group(1)}"
        slug = _RE_NON_ALNUM.sub("-", table_title.lower()).strip("-")
        return f"table-{slug[:40]}"
    return f"table-p{page}-{table_idx}"


def render_markdown(header: list[str], rows: list[dict], table_title: str) -> str:
    """Render the full table as GitHub-flavored markdown."""
    lines = []
    if table_title:
        lines.append(f"**{table_title}**")
        lines.append("")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join("---" for _ in header) + "|")
    for row in rows:
        cells = [str(row.get(h, "") or "").replace("\n", " ").strip() for h in header]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def build_general_tables_db(
    tables_jsonl: Path,
    registry_path: Path,
    db_path: Path,
) -> int:
    """Build the general_tables SQLite table. Returns number of tables inserted."""
    registry = json.loads(registry_path.read_text())
    doc_info = registry[0]
    doc_id = doc_info["doc_id"]
    chip_part = doc_info["chip_part"]

    db_path.parent.mkdir(parents=True, exist_ok=True)

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.executescript(CREATE_GENERAL_TABLES)

    count = 0
    seen_ids: set[str] = set()
    with tables_jsonl.open(encoding="utf-8") as f:
        for line in f:
            table = json.loads(line)
            header = [str(h).strip() for h in table.get("header", []) if h]
            rows = table.get("rows", [])

            # Register tables are stored in registers/bit_fields, not here.
            if table.get("is_register") or any(_RE_BIT_HEADER.match(h) for h in header):
                continue
            if not header or not rows:
                continue

            table_title = table.get("table_title", "")
            page = table["page"]
            table_id = make_table_id(table_title, page, table.get("table_idx", 0))
            # Disambiguate tables that collide on the same derived id (e.g. no title, same page)
            base_id = table_id
            suffix = 1
            while table_id in seen_ids:
                table_id = f"{base_id}-{suffix}"
                suffix += 1
            seen_ids.add(table_id)

            markdown = render_markdown(header, rows, table_title)
            section_path = table.get("section_path") or "§UNKNOWN"

            cur.execute(
                """
                INSERT OR REPLACE INTO general_tables
                  (table_id, title, markdown, doc_id, chip_part, section_path, page)
                VALUES (?,?,?,?,?,?,?)
                """,
                (table_id, table_title, markdown, doc_id, chip_part, section_path, page),
            )
            count += 1

    con.commit()
    con.close()
    return count


if __name__ == "__main__":
    tables_jsonl = Path("data/parsed/tables.jsonl")
    registry_path = Path("data/registry.json")
    db_path = Path("data/store/registers.db")

    print("Building general_tables database ...")
    n = build_general_tables_db(tables_jsonl, registry_path, db_path)
    print(f"Inserted {n} general tables into {db_path}")

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM general_tables").fetchone()[0]
    print(f"\nSELECT COUNT(*) FROM general_tables = {total}")
    row = cur.execute(
        "SELECT table_id, title FROM general_tables LIMIT 3"
    ).fetchall()
    for r in row:
        print(f"  {r[0]}: {r[1]}")
    con.close()
