"""
Build the SQLite register database from parsed tables.

Reads:
  data/parsed/tables.jsonl   (from parser_tables.py)
  data/parsed/pages.jsonl    (from parser_text.py) — for register header prose lookup
  data/registry.json

Writes:
  data/store/registers.db
"""

import json
import re
import sqlite3
from pathlib import Path


# ── Schema ────────────────────────────────────────────────────────────────────

CREATE_REGISTERS = """
CREATE TABLE IF NOT EXISTS registers (
    peripheral    TEXT,
    register_name TEXT,
    address       TEXT,
    size_bits     INTEGER,
    reset_value   TEXT,
    access        TEXT,
    doc_id        TEXT,
    revision      TEXT,
    section_path  TEXT,
    page_start    INTEGER,
    page_end      INTEGER,
    json          TEXT,
    PRIMARY KEY (peripheral, register_name)
);
"""

CREATE_BIT_FIELDS = """
CREATE TABLE IF NOT EXISTS bit_fields (
    peripheral    TEXT,
    register_name TEXT,
    bits          TEXT,
    symbol        TEXT,
    access        TEXT,
    reset         TEXT,
    description   TEXT,
    FOREIGN KEY (peripheral, register_name) REFERENCES registers(peripheral, register_name)
);
"""

# ── Helpers ───────────────────────────────────────────────────────────────────

# Patterns to extract register metadata from prose above a table
_RE_REG_NAME = re.compile(
    r"\b([A-Z][A-Z0-9_]{2,}(?:n|m)?)\b"  # all-caps token, optional trailing n/m
)
_RE_ADDRESS = re.compile(
    r"(?:Address(?:es)?|Addr\.?)\s*[:\–\-]?\s*(0x[0-9A-Fa-f]{4,}(?:\s*\+\s*[0-9A-Fx×]+\s*×?\s*n?)?)",
    re.IGNORECASE,
)
_RE_RESET = re.compile(
    r"(?:Reset\s*[Vv]alue|Initial\s*[Vv]alue|Reset)\s*[:\–\-]?\s*(0x[0-9A-Fa-f]+|[0-9A-Fa-f]+H|\b0b[01]+\b)",
    re.IGNORECASE,
)
_RE_ACCESS = re.compile(
    r"\b(R/W|R/W1C|R/W1S|RO|WO|R|W)\b",
)
_RE_PERIPHERAL_SECTION = re.compile(
    r"§?\s*(\d+(?:\.\d+)?)\s+([A-Z][A-Za-z0-9 _/\-]+?)(?:\s+\(|$)",
)


def _build_page_index(pages_jsonl: Path) -> dict[int, list[dict]]:
    """Return dict mapping page_number -> list of text block dicts."""
    index: dict[int, list[dict]] = {}
    with pages_jsonl.open(encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)
            index.setdefault(b["page"], []).append(b)
    return index


def _extract_register_header(page_blocks: list[dict], table_page: int) -> dict:
    """Heuristically extract register name/address/reset from same-page prose."""
    combined = " ".join(b["text"] for b in page_blocks)

    name_match = _RE_REG_NAME.findall(combined)
    # Take the first all-caps token that looks like a register name (>= 4 chars or ends in n)
    reg_name = ""
    for candidate in name_match:
        if len(candidate) >= 4 or candidate.endswith("n"):
            reg_name = candidate
            break

    addr_match = _RE_ADDRESS.search(combined)
    address = addr_match.group(1).strip() if addr_match else ""

    reset_match = _RE_RESET.search(combined)
    reset_value = reset_match.group(1).strip() if reset_match else ""

    access_match = _RE_ACCESS.search(combined)
    access = access_match.group(1) if access_match else ""

    section_path = page_blocks[0].get("section_path") if page_blocks else None
    peripheral = _guess_peripheral(section_path or "")

    return {
        "register_name": reg_name,
        "address": address,
        "reset_value": reset_value,
        "access": access,
        "section_path": section_path,
        "peripheral": peripheral,
    }


def _guess_peripheral(section_path: str) -> str:
    """Extract peripheral name from section_path (e.g. 'AGT', 'SCI', 'PORT')."""
    if not section_path:
        return "UNKNOWN"
    # Last section segment often contains peripheral name
    parts = section_path.split(">")
    for part in reversed(parts):
        m = re.search(r"\b([A-Z]{2,8})\b", part)
        if m:
            return m.group(1)
    return "UNKNOWN"


def _parse_bit_field_rows(rows: list[dict]) -> list[dict]:
    """Convert raw table row dicts to normalised bit-field records."""
    fields = []
    # Normalise column names: lowercase + strip
    for row in rows:
        norm = {k.lower().strip(): v for k, v in row.items()}
        bits = norm.get("bit", norm.get("bits", ""))
        symbol = norm.get("symbol", norm.get("bit name", norm.get("name", "")))
        access = norm.get("r/w", norm.get("access", ""))
        reset = norm.get("reset", norm.get("value", ""))
        # PDF register tables use the column name "Function".
        # We store it in the existing SQLite column named "description"
        # to preserve compatibility with the rest of the code.
        description = norm.get("function", norm.get("description", norm.get("desc", "")))
        if not bits and not symbol:
            continue
        fields.append({
            "bits": bits.strip(),
            "symbol": symbol.strip(),
            "access": access.strip(),
            "reset": reset.strip(),
            "description": description.strip(),
        })
    return fields


# ── Main ──────────────────────────────────────────────────────────────────────

def build_register_db(
    tables_jsonl: Path,
    pages_jsonl: Path,
    registry_path: Path,
    db_path: Path,
) -> int:
    """Build SQLite register DB. Returns number of registers inserted."""
    registry = json.loads(registry_path.read_text())
    doc_info = registry[0]
    doc_id = doc_info["doc_id"]
    revision = doc_info["revision"]

    db_path.parent.mkdir(parents=True, exist_ok=True)
    page_index = _build_page_index(pages_jsonl)

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    cur.executescript(CREATE_REGISTERS + CREATE_BIT_FIELDS)

    count = 0
    with tables_jsonl.open(encoding="utf-8") as f:
        for line_no, line in enumerate(f):
            table = json.loads(line)
            # Only process register tables (general/lookup tables are chunked separately)
            if not table.get("is_register", True):
                continue
            page = table["page"]
            rows = table.get("rows", [])
            page_blocks = page_index.get(page, [])

            # Use pre-extracted register_name/peripheral from parser_tables if available,
            # fall back to heuristic prose extraction only when missing.
            reg_name = table.get("register_name") or ""
            peripheral = table.get("peripheral") or ""
            section_path = table.get("section_path") or ""

            if not reg_name or not peripheral or not section_path:
                header_info = _extract_register_header(page_blocks, page)
                reg_name = reg_name or header_info["register_name"] or f"REG_{line_no}"
                peripheral = peripheral or header_info["peripheral"]
                section_path = section_path or header_info["section_path"] or ""

            # Still extract address/reset from prose (not in tables.jsonl)
            combined = " ".join(b["text"] for b in page_blocks)
            addr_match = _RE_ADDRESS.search(combined)
            address = addr_match.group(1).strip() if addr_match else ""
            reset_match = _RE_RESET.search(combined)
            reset_value = reset_match.group(1).strip() if reset_match else ""
            access_match = _RE_ACCESS.search(combined)
            access = access_match.group(1) if access_match else ""

            bit_fields = _parse_bit_field_rows(rows)
            full_json = json.dumps({
                "register_name": reg_name,
                "address": address,
                "reset_value": reset_value,
                "access": access,
                "bit_fields": bit_fields,
            })

            # Upsert register
            cur.execute(
                """
                INSERT OR REPLACE INTO registers
                  (peripheral, register_name, address, size_bits, reset_value, access,
                   doc_id, revision, section_path, page_start, page_end, json)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    peripheral, reg_name, address, 32, reset_value, access,
                    doc_id, revision, section_path, page, page, full_json,
                ),
            )

            for bf in bit_fields:
                cur.execute(
                    """
                    INSERT INTO bit_fields
                      (peripheral, register_name, bits, symbol, access, reset, description)
                    VALUES (?,?,?,?,?,?,?)
                    """,
                    (peripheral, reg_name, bf["bits"], bf["symbol"],
                     bf["access"], bf["reset"], bf["description"]),
                )
            count += 1

    con.commit()
    con.close()
    return count


if __name__ == "__main__":
    tables_jsonl = Path("data/parsed/tables.jsonl")
    pages_jsonl = Path("data/parsed/pages.jsonl")
    registry_path = Path("data/registry.json")
    db_path = Path("data/store/registers.db")

    print("Building register database ...")
    n = build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path)
    print(f"Inserted {n} registers into {db_path}")

    # Checkpoint queries
    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    total = cur.execute("SELECT COUNT(*) FROM registers").fetchone()[0]
    print(f"\nSELECT COUNT(*) FROM registers = {total}  (target >= 50)")

    row = cur.execute(
        "SELECT r.register_name, r.address, COUNT(bf.bits) FROM registers r "
        "LEFT JOIN bit_fields bf ON r.peripheral=bf.peripheral AND r.register_name=bf.register_name "
        "WHERE r.register_name LIKE 'SCKCR%' GROUP BY r.peripheral, r.register_name"
    ).fetchone()
    if row:
        print(f"SCKCR check: {row[0]}, address={row[1]}, bit_fields={row[2]}")
    else:
        print("SCKCR: not found (may be indexed under a different name)")
    con.close()