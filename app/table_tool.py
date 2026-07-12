"""
get_table(table_id, chip_part) — deterministic SQLite lookup for general tables.

Mirrors register_tool.py's pattern: search_um finds a table_summary/table_row
hit carrying a table_id, get_table returns the exact, full markdown table —
vector search never has to reconstruct table content.
"""

import json
import sqlite3
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_DB_PATH = _ROOT / "data/store/registers.db"
_REGISTRY_PATH = _ROOT / "data/registry.json"


def _load_registry() -> dict[str, dict]:
    """Return dict keyed by chip_part."""
    registry = json.loads(_REGISTRY_PATH.read_text())
    return {r["chip_part"]: r for r in registry}


def _make_citation(doc_id: str, revision: str, section_path: str, page: int) -> str:
    return f"【{doc_id} Rev.{revision} | {section_path} | p.{page}】"


def get_table(table_id: str, chip_part: str, db_path: Path = _DB_PATH) -> dict | None:
    """Look up a general table by table_id for a given chip_part.

    Returns None when the chip_part is unknown or no matching table exists.
    """
    registry = _load_registry()
    doc_info = registry.get(chip_part)
    if doc_info is None:
        return None

    doc_id = doc_info["doc_id"]
    revision = doc_info["revision"]

    if not db_path.exists():
        return None

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    cur.execute(
        """
        SELECT table_id, title, markdown, section_path, page
        FROM general_tables
        WHERE doc_id = ? AND table_id = ?
        """,
        (doc_id, table_id),
    )
    row = cur.fetchone()
    con.close()

    if row is None:
        return None

    section_path = row["section_path"] or "§UNKNOWN"
    page = row["page"] or 0

    return {
        "table_id": row["table_id"],
        "title": row["title"],
        "markdown": row["markdown"],
        "section_path": section_path,
        "page": page,
        "citation": _make_citation(doc_id, revision, section_path, page),
    }


if __name__ == "__main__":
    # Quick smoke test
    import sys

    tid = sys.argv[1] if len(sys.argv) > 1 else "table-3.1"
    chip = sys.argv[2] if len(sys.argv) > 2 else "RA6M4"

    result = get_table(tid, chip)
    if result:
        print(json.dumps(result, indent=2, ensure_ascii=False))
    else:
        print(f"No results for {tid!r} in {chip}")
