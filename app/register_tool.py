"""
register_lookup(name, chip_part) — live SQLite lookup against the Smart Manual DB.

Returns a list[dict] because a register name can appear in multiple peripherals
(or under duplicate module aliases, e.g. R_SYSC/R_SYSTEM at the same address).
Each record includes a pre-formatted `citation` field.

Registers are queried live from the Smart Manual DB — there is no local
registers.db import step. The bit-function table (Bit | Symbol | Function | R/W)
is already embedded in registerList.display_data for every register, so
bitList is not joined; parsing registerList.display_data alone is sufficient
and avoids families (e.g. IELSRn) that have zero matching bitList rows.
"""

import json
import sqlite3
from pathlib import Path

from bs4 import BeautifulSoup
from rapidfuzz import process, fuzz

from app.smart_manual_locator import locate

# ── SQLite connection cache, keyed by chip_part ───────────────────────────────

_connections: dict[str, sqlite3.Connection] = {}


def _get_connection(chip_part: str) -> sqlite3.Connection:
    """Return a cached SQLite connection for chip_part, opening it on first call."""
    con = _connections.get(chip_part)
    if con is None:
        db_path = locate(chip_part)
        con = sqlite3.connect(str(db_path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        _connections[chip_part] = con
    return con


def _make_citation(chip_part: str, register_symbol_name: str, register_name: str) -> str:
    return f"【{chip_part} Smart Manual | {register_symbol_name} : {register_name}】"


# ── Bit-table parsing ──────────────────────────────────────────────────────────

def _parse_bit_fields(display_data: str) -> list[dict]:
    """Extract bit fields from the frame-all (Bit|Symbol|Function|R/W) table.

    Handles rowspan by forward-filling the bit/symbol/access columns across the
    enumerated-value continuation rows.
    """
    soup = BeautifulSoup(display_data, "html.parser")
    table = soup.find("table", class_="frame-all")
    if table is None:
        return []

    fields: list[dict] = []
    current: dict | None = None

    for tr in table.find_all("tr"):
        cells = tr.find_all(["td", "th"])
        if not cells:
            continue
        texts = [c.get_text(strip=True) for c in cells]
        if texts and texts[0] == "Bit":
            continue  # header row

        # A new bit-field row has 4 cells: Bit, Symbol, Function, R/W.
        # A continuation (enumerated value) row has 1-2 cells: value, description.
        if len(cells) >= 4:
            bits, symbol, function, access = texts[0], texts[1], texts[2], texts[3]
            current = {
                "bits": bits,
                "symbol": symbol,
                "access": access,
                "description": function,
                "enum_values": [],
            }
            fields.append(current)
        elif current is not None and texts:
            current["enum_values"].append(" ".join(t for t in texts if t))

    for f in fields:
        if f["enum_values"]:
            f["description"] = f["description"] + " — " + "; ".join(f["enum_values"])
        del f["enum_values"]

    return fields


def _parse_reset_value(display_data: str) -> str:
    """Best-effort extraction of the register's reset value from the bit-position table."""
    soup = BeautifulSoup(display_data, "html.parser")
    table = soup.find("table", class_="frame-none")
    if table is None:
        return ""

    reset_bits: list[str] = []
    for tr in table.find_all("tr"):
        cells = [c.get_text(strip=True) for c in tr.find_all("td")]
        if cells and cells[0] == "Value after reset:":
            reset_bits.extend(c for c in cells[1:] if c)

    if not reset_bits:
        return ""
    return "0b" + "".join(reset_bits)


# ── Lookup ─────────────────────────────────────────────────────────────────────

def _resolve_name(con: sqlite3.Connection, name: str) -> str:
    """Fuzzy-match name against register_symbol_name if there's no exact/prefix hit.

    Handles PDF-era names that differ from the Smart Manual's FSP convention
    (e.g. SCKCR -> SCKDIVCR).
    """
    cur = con.execute(
        "SELECT 1 FROM registerList WHERE register_symbol_name = ? OR register_symbol_name LIKE ? LIMIT 1",
        (name, f"{name}%"),
    )
    if cur.fetchone() is not None:
        return name

    cur = con.execute("SELECT DISTINCT register_symbol_name FROM registerList")
    all_names = [r["register_symbol_name"] for r in cur.fetchall()]
    match = process.extractOne(name, all_names, scorer=fuzz.WRatio, score_cutoff=80)
    return match[0] if match else name


def register_lookup(name: str, chip_part: str) -> list[dict]:
    """Look up a register by name for a given chip_part.

    Searches exact match and LIKE '{name}%' to handle indexed variants (e.g. IELSRn).
    Deduplicates module aliases that share the same base address (e.g. R_SYSC/R_SYSTEM).
    Returns [] for unknown names or a missing Smart Manual DB.
    """
    try:
        con = _get_connection(chip_part)
    except FileNotFoundError:
        return []

    resolved_name = _resolve_name(con, name)

    cur = con.execute(
        """
        SELECT r.register_symbol_name, r.register_name, r.address, r.display_data,
               r.module_symbol_name, m.module_base_address
        FROM registerList r
        LEFT JOIN moduleList m ON r.module_symbol_name = m.module_symbol_name
        WHERE r.register_symbol_name = ? OR r.register_symbol_name LIKE ?
        ORDER BY r.register_symbol_name
        """,
        (resolved_name, f"{resolved_name}%"),
    )
    rows = cur.fetchall()

    results = []
    seen_addresses: set[tuple[str, str]] = set()
    for row in rows:
        dedup_key = (row["register_symbol_name"], row["address"])
        if dedup_key in seen_addresses:
            continue
        seen_addresses.add(dedup_key)

        bit_fields = _parse_bit_fields(row["display_data"])
        reset_value = _parse_reset_value(row["display_data"])
        access = bit_fields[0]["access"] if len(bit_fields) == 1 else ""

        results.append({
            "peripheral": row["module_symbol_name"] or "",
            "register_name": row["register_symbol_name"],
            "full_name": row["register_name"],
            "address": row["address"],
            "reset_value": reset_value,
            "access": access,
            "bit_fields": bit_fields,
            "citation": _make_citation(chip_part, row["register_symbol_name"], row["register_name"]),
        })

    return results


if __name__ == "__main__":
    import sys

    name = sys.argv[1] if len(sys.argv) > 1 else "SCKDIVCR"
    chip = sys.argv[2] if len(sys.argv) > 2 else "RA6M4"

    results = register_lookup(name, chip)
    if results:
        for r in results:
            print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print(f"No results for {name!r} in {chip}")
