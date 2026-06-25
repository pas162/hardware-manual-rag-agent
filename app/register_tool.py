"""
register_lookup(name, chip_part) — deterministic SQLite lookup.

Returns a list[dict] because a register name can appear in multiple peripherals.
Each record includes a pre-formatted `citation` field.
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


def register_lookup(name: str, chip_part: str, db_path: Path = _DB_PATH) -> list[dict]:
    """Look up a register by name for a given chip_part.

    Searches both exact match and LIKE '{name}%' to handle indexed variants (e.g. IELSRn).
    Returns [] for unknown names.
    """
    registry = _load_registry()
    doc_info = registry.get(chip_part)
    if doc_info is None:
        return []

    doc_id = doc_info["doc_id"]
    revision = doc_info["revision"]

    if not db_path.exists():
        return []

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.cursor()

    # Try exact match first, then prefix match
    cur.execute(
        """
        SELECT peripheral, register_name, address, size_bits, reset_value, access,
               section_path, page_start, page_end
        FROM registers
        WHERE doc_id = ? AND (register_name = ? OR register_name LIKE ?)
        ORDER BY peripheral, register_name
        """,
        (doc_id, name, f"{name}%"),
    )
    rows = cur.fetchall()

    results = []
    for row in rows:
        section_path = row["section_path"] or "§UNKNOWN"
        page = row["page_start"] or 0

        # Fetch bit fields
        cur2 = con.cursor()
        cur2.execute(
            """
            SELECT bits, symbol, access, reset, description
            FROM bit_fields
            WHERE peripheral = ? AND register_name = ?
            ORDER BY rowid
            """,
            (row["peripheral"], row["register_name"]),
        )
        bit_fields = [
            {
                "bits": r["bits"],
                "symbol": r["symbol"],
                "access": r["access"],
                "reset": r["reset"],
                "description": r["description"],
            }
            for r in cur2.fetchall()
        ]

        results.append({
            "peripheral": row["peripheral"],
            "register_name": row["register_name"],
            "address": row["address"],
            "size_bits": row["size_bits"],
            "reset_value": row["reset_value"],
            "access": row["access"],
            "section_path": section_path,
            "page": page,
            "bit_fields": bit_fields,
            "citation": _make_citation(doc_id, revision, section_path, page),
        })

    con.close()
    return results


if __name__ == "__main__":
    # Quick smoke test
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "SCKCR"
    chip = sys.argv[2] if len(sys.argv) > 2 else "RA6M4"

    results = register_lookup(name, chip)
    if results:
        for r in results:
            print(json.dumps(r, indent=2))
    else:
        print(f"No results for {name!r} in {chip}")
