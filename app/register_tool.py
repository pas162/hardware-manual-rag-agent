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
            WHERE peripheral = ? AND register_name = ? AND doc_id = ?
            ORDER BY rowid
            """,
            (row["peripheral"], row["register_name"], doc_id),
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


def _bit_matches(bits: str, bit_or_symbol: str) -> bool:
    """True if bit_or_symbol names a single bit or falls within a bit range.

    `bits` is stored as either a single index ("16") or a range ("8:0", high:low).
    Matching is by exact single-bit index only (e.g. "5" matches bits="5" or
    bits="8:0" when 5 falls in [0,8]); non-numeric input never matches.
    """
    if not bit_or_symbol.isdigit():
        return False
    target = int(bit_or_symbol)

    if ":" in bits:
        try:
            hi, lo = (int(x) for x in bits.split(":", 1))
        except ValueError:
            return False
        return lo <= target <= hi

    try:
        return int(bits) == target
    except ValueError:
        return False


def query_register_field(
    register_name: str, bit_or_symbol: str, chip_part: str, db_path: Path = _DB_PATH
) -> dict | None:
    """Look up a single bit field within one register, by bit index/range or symbol name.

    Matches bit_or_symbol against bit_fields.symbol (case-insensitive exact match)
    first, then against bit_fields.bits (exact index or within a "hi-lo" range).
    Returns None when the register, chip_part, or field is unknown.
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
        SELECT peripheral, register_name, address, section_path, page_start
        FROM registers
        WHERE doc_id = ? AND register_name = ?
        """,
        (doc_id, register_name),
    )
    reg_row = cur.fetchone()
    if reg_row is None:
        con.close()
        return None

    cur.execute(
        """
        SELECT bits, symbol, access, reset, description
        FROM bit_fields
        WHERE peripheral = ? AND register_name = ? AND doc_id = ?
        ORDER BY rowid
        """,
        (reg_row["peripheral"], reg_row["register_name"], doc_id),
    )
    field_rows = cur.fetchall()
    con.close()

    match = None
    for row in field_rows:
        if row["symbol"] and row["symbol"].lower() == bit_or_symbol.lower():
            match = row
            break
    if match is None:
        for row in field_rows:
            if _bit_matches(row["bits"], bit_or_symbol):
                match = row
                break
    if match is None:
        return None

    section_path = reg_row["section_path"] or "§UNKNOWN"
    page = reg_row["page_start"] or 0

    return {
        "peripheral": reg_row["peripheral"],
        "register_name": reg_row["register_name"],
        "address": reg_row["address"],
        "bits": match["bits"],
        "symbol": match["symbol"],
        "access": match["access"],
        "reset": match["reset"],
        "description": match["description"],
        "section_path": section_path,
        "page": page,
        "citation": _make_citation(doc_id, revision, section_path, page),
    }


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
