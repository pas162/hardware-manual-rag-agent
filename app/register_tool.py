"""
register_lookup(name, chip_part) — live SQLite lookup against the Smart Manual DB.

Returns a list[dict] because a register name can appear in multiple peripherals
(or under duplicate module aliases, e.g. R_SYSC/R_SYSTEM at the same address).
Each record includes a pre-formatted `citation` field.

Search strategy (mirrors the Smart Manual VS Code extension):
  1. Try registerList first  — exact + LIKE prefix match on register_symbol_name.
  2. If no register hit, try bitList — exact + LIKE prefix match on bit_symbol_name.
     • bitList rows whose display_data starts with "refer_to <module> <reg> <bit>"
       are reference keys; they are resolved to the real row automatically.
     • A bit result returns the parent register's full bit-table (from registerList)
       with the matched bit highlighted, plus the bit's own description from its
       display_data.
  3. Fuzzy-match fallback for both tables when no exact/prefix hit is found.
"""

import json
import re
import sqlite3
import sys
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


def _make_citation(chip_part: str, symbol: str, full_name: str) -> str:
    return f"【{chip_part} Smart Manual | {symbol} : {full_name}】"


# ── Reference-key resolution (bitList) ────────────────────────────────────────
# When bitList.display_data starts with "refer_to <module> <reg> <bit>", the row
# is an alias that points to the canonical row.  Mirror the extension's logic in
# referenceKey.ts / queryBit.ts → resolveBit().

_REFER_TO_RE = re.compile(r"^refer_to\s+(\S+)\s+(\S+)\s+(\S+)$")


def _resolve_bit_display_data(con: sqlite3.Connection, display_data: str) -> str:
    """If display_data is a reference key, follow it once and return the real HTML.
    Returns the original string unchanged when it is not a reference key.
    """
    m = _REFER_TO_RE.match(display_data.strip())
    if m is None:
        return display_data
    module_sym, reg_sym, bit_sym = m.group(1), m.group(2), m.group(3)
    row = con.execute(
        """SELECT display_data FROM bitList
           WHERE module_symbol_name = ?
             AND register_symbol_name LIKE ?
             AND bit_symbol_name = ?
           LIMIT 1""",
        (module_sym, f"{reg_sym}%", bit_sym),
    ).fetchone()
    # Only replace if the resolved row is itself real HTML (not another ref key)
    if row and row["display_data"] and not row["display_data"].strip().startswith("refer_to"):
        return row["display_data"]
    return display_data


# ── HTML parsing helpers ───────────────────────────────────────────────────────

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

        # A new bit-field row has ≥4 cells: Bit, Symbol, Function, R/W.
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


def _parse_bit_description(display_data: str) -> str:
    """Extract the prose description of a single bit from bitList.display_data.

    The <dl> block after the bit table contains a <dt> (bit name) and <dd>
    (description sentence).  Falls back to the table function cell text.
    """
    soup = BeautifulSoup(display_data, "html.parser")
    dd = soup.find("dd")
    if dd:
        return dd.get_text(" ", strip=True)
    # Fallback: grab the function column from the frame-all table
    table = soup.find("table", class_="frame-all")
    if table:
        for tr in table.find_all("tr"):
            cells = tr.find_all(["td", "th"])
            texts = [c.get_text(strip=True) for c in cells]
            if len(texts) >= 3 and texts[0] != "Bit":
                return texts[2]
    return ""


# ── Name resolution (fuzzy fallback) ──────────────────────────────────────────

def _resolve_register_name(con: sqlite3.Connection, name: str) -> str:
    """Return the best-matching register_symbol_name, with fuzzy fallback."""
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


def _resolve_bit_name(con: sqlite3.Connection, name: str) -> str:
    """Return the best-matching bit_symbol_name, with fuzzy fallback."""
    cur = con.execute(
        "SELECT 1 FROM bitList WHERE bit_symbol_name = ? OR bit_symbol_name LIKE ? LIMIT 1",
        (name, f"{name}%"),
    )
    if cur.fetchone() is not None:
        return name
    cur = con.execute("SELECT DISTINCT bit_symbol_name FROM bitList")
    all_names = [r["bit_symbol_name"] for r in cur.fetchall()]
    match = process.extractOne(name, all_names, scorer=fuzz.WRatio, score_cutoff=80)
    return match[0] if match else name


# ── Register lookup (registerList) ────────────────────────────────────────────

def _lookup_register(con: sqlite3.Connection, name: str, chip_part: str) -> list[dict]:
    """Query registerList, preferring exact matches before prefix fallback."""
    resolved = _resolve_register_name(con, name)
    exact_rows = con.execute(
        """
        SELECT r.register_symbol_name, r.register_name, r.address, r.display_data,
               r.module_symbol_name, m.module_base_address
        FROM   registerList r
        LEFT JOIN moduleList m ON r.module_symbol_name = m.module_symbol_name
        WHERE  r.register_symbol_name = ?
        ORDER  BY r.register_symbol_name
        """,
        (resolved,),
    ).fetchall()
    rows = exact_rows if exact_rows else con.execute(
        """
        SELECT r.register_symbol_name, r.register_name, r.address, r.display_data,
               r.module_symbol_name, m.module_base_address
        FROM   registerList r
        LEFT JOIN moduleList m ON r.module_symbol_name = m.module_symbol_name
        WHERE  r.register_symbol_name LIKE ?
        ORDER  BY r.register_symbol_name
        """,
        (f"{resolved}%",),
    ).fetchall()

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for row in rows:
        key = (row["register_symbol_name"], row["address"])
        if key in seen:
            continue
        seen.add(key)

        bit_fields = _parse_bit_fields(row["display_data"])
        reset_value = _parse_reset_value(row["display_data"])
        access = bit_fields[0]["access"] if len(bit_fields) == 1 else ""

        results.append({
            "result_type": "register",
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


# ── Bit lookup (bitList) ───────────────────────────────────────────────────────

_BIT_RESULT_LIMIT = 20   # cap bit-lookup results to avoid flooding the caller


def _lookup_bit(con: sqlite3.Connection, name: str, chip_part: str) -> list[dict]:
    """Query bitList, preferring exact matches before prefix fallback.

    For each matching bit:
    - Resolve reference keys (refer_to …) to the canonical display_data.
    - Fetch the parent register's full bit-table from registerList so the
      caller sees the complete register context, not just the single bit.
    - Mark the matched bit with matched_bit = True in the bit_fields list.
    """
    resolved = _resolve_bit_name(con, name)
    exact_bit_rows = con.execute(
        """
        SELECT bit_symbol_name, bit_name, bit, register_symbol_name,
               module_symbol_name, address, display_data
        FROM   bitList
        WHERE  bit_symbol_name = ?
        ORDER  BY module_symbol_name, register_symbol_name, bit_symbol_name
        LIMIT  ?
        """,
        (resolved, _BIT_RESULT_LIMIT),
    ).fetchall()
    bit_rows = exact_bit_rows if exact_bit_rows else con.execute(
        """
        SELECT bit_symbol_name, bit_name, bit, register_symbol_name,
               module_symbol_name, address, display_data
        FROM   bitList
        WHERE  bit_symbol_name LIKE ?
        ORDER  BY module_symbol_name, register_symbol_name, bit_symbol_name
        LIMIT  ?
        """,
        (f"{resolved}%", _BIT_RESULT_LIMIT),
    ).fetchall()

    results: list[dict] = []
    seen: set[tuple[str, str]] = set()   # (register_symbol_name, address)

    for brow in bit_rows:
        # Resolve reference key if needed
        raw_dd = brow["display_data"] or ""
        bit_display = _resolve_bit_display_data(con, raw_dd)
        bit_description = _parse_bit_description(bit_display)

        # Fetch the parent register from registerList for full context
        reg_row = con.execute(
            """
            SELECT r.register_symbol_name, r.register_name, r.address,
                   r.display_data, r.module_symbol_name
            FROM   registerList r
            WHERE  r.register_symbol_name = ?
              AND  r.module_symbol_name   = ?
            LIMIT 1
            """,
            (brow["register_symbol_name"], brow["module_symbol_name"]),
        ).fetchone()

        if reg_row is None:
            # No parent register found — return a minimal bit-only result
            results.append({
                "result_type": "bit",
                "peripheral": brow["module_symbol_name"] or "",
                "register_name": brow["register_symbol_name"],
                "full_name": brow["bit_name"] or "",
                "address": brow["address"],
                "reset_value": "",
                "access": "",
                "matched_bit": {
                    "symbol": brow["bit_symbol_name"],
                    "bits": brow["bit"],
                    "description": bit_description,
                },
                "bit_fields": [],
                "citation": _make_citation(
                    chip_part,
                    f"{brow['register_symbol_name']}.{brow['bit_symbol_name']}",
                    brow["bit_name"] or brow["bit_symbol_name"],
                ),
            })
            continue

        dedup_key = (reg_row["register_symbol_name"], reg_row["address"])
        if dedup_key in seen:
            continue
        seen.add(dedup_key)

        bit_fields = _parse_bit_fields(reg_row["display_data"])
        reset_value = _parse_reset_value(reg_row["display_data"])

        # Mark the matched bit(s) in the full bit-field list
        for bf in bit_fields:
            if bf["symbol"] == brow["bit_symbol_name"]:
                bf["matched"] = True

        results.append({
            "result_type": "bit",
            "peripheral": reg_row["module_symbol_name"] or "",
            "register_name": reg_row["register_symbol_name"],
            "full_name": reg_row["register_name"],
            "address": reg_row["address"],
            "reset_value": reset_value,
            "access": "",
            "matched_bit": {
                "symbol": brow["bit_symbol_name"],
                "bits": brow["bit"],
                "description": bit_description,
            },
            "bit_fields": bit_fields,
            "citation": _make_citation(
                chip_part,
                f"{reg_row['register_symbol_name']}.{brow['bit_symbol_name']}",
                f"{reg_row['register_name']} — bit {brow['bit_symbol_name']}",
            ),
        })

    return results


# ── Public API ─────────────────────────────────────────────────────────────────

def register_lookup(name: str, chip_part: str) -> list[dict]:
    """Look up a register or bit by name for a given chip_part.

    Search order:
      1. registerList  — exact + LIKE prefix on register_symbol_name (+ fuzzy fallback)
      2. bitList       — exact + LIKE prefix on bit_symbol_name (+ fuzzy fallback),
                         only when step 1 returns nothing.

    Returns [] for unknown names or a missing Smart Manual DB.
    Each result dict contains a `result_type` field: "register" or "bit".
    """
    try:
        con = _get_connection(chip_part)
    except FileNotFoundError as exc:
        print(
            f"[hardware-um] register_lookup unavailable for {chip_part}: {exc}",
            file=sys.stderr,
            flush=True,
        )
        return []

    results = _lookup_register(con, name, chip_part)
    if not results:
        results = _lookup_bit(con, name, chip_part)
    return results


if __name__ == "__main__":
    import sys

    # Windows consoles default to cp1252, which can't encode the 【 】 citation
    # brackets — force UTF-8 on stdout so CLI testing doesn't crash.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass

    name = sys.argv[1] if len(sys.argv) > 1 else "SCKDIVCR"
    chip = sys.argv[2] if len(sys.argv) > 2 else "RA6M4"

    results = register_lookup(name, chip)
    if results:
        for r in results:
            print(json.dumps(r, indent=2, ensure_ascii=False))
    else:
        print(f"No results for {name!r} in {chip}")