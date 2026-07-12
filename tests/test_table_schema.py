"""Tests for ingest/table_schema.py — general_tables SQLite storage (R1)."""

import json
import sqlite3

from ingest.table_schema import build_general_tables_db, make_table_id, render_markdown


def test_make_table_id_from_title():
    assert make_table_id("Table 3.1 Selection of operating modes", 91, 0) == "table-3.1"
    assert make_table_id("Table 4.1 Capacity of the code flash memory", 93, 0) == "table-4.1"


def test_make_table_id_fallback_without_title():
    assert make_table_id("", 12, 2) == "table-p12-2"


def test_render_markdown_basic():
    header = ["Mode-setting pin (MD)", "Operating mode"]
    rows = [
        {"Mode-setting pin (MD)": "1", "Operating mode": "Single-chip mode"},
        {"Mode-setting pin (MD)": "0", "Operating mode": "SCI / USB boot mode"},
    ]
    md = render_markdown(header, rows, "Table 3.1 Selection of operating modes")

    assert "**Table 3.1 Selection of operating modes**" in md
    assert "| Mode-setting pin (MD) | Operating mode |" in md
    assert "| 1 | Single-chip mode |" in md
    assert "| 0 | SCI / USB boot mode |" in md


def test_build_general_tables_db_excludes_register_tables(tmp_path):
    tables_jsonl = tmp_path / "tables.jsonl"
    registry_path = tmp_path / "registry.json"
    db_path = tmp_path / "registers.db"

    records = [
        {
            "page": 91, "table_idx": 0, "section_path": "§3.3.1",
            "is_register": False, "table_title": "Table 3.1 Selection of operating modes",
            "header": ["Mode-setting pin (MD)", "Operating mode"],
            "rows": [{"Mode-setting pin (MD)": "1", "Operating mode": "Single-chip mode"}],
        },
        {
            "page": 50, "table_idx": 0, "section_path": "§5.1",
            "is_register": True, "table_title": "SCKCR : System Clock Control Register",
            "header": ["Bit", "Symbol", "R/W"],
            "rows": [{"Bit": "0", "Symbol": "CKSEL", "R/W": "R/W"}],
        },
    ]
    tables_jsonl.write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )
    registry_path.write_text(json.dumps([
        {"doc_id": "TESTDOC", "revision": "1.00", "chip_part": "RA6M4", "path": "x.pdf"}
    ]), encoding="utf-8")

    n = build_general_tables_db(tables_jsonl, registry_path, db_path)
    assert n == 1

    con = sqlite3.connect(str(db_path))
    cur = con.cursor()
    rows = cur.execute("SELECT table_id, title, doc_id, chip_part, page FROM general_tables").fetchall()
    con.close()

    assert len(rows) == 1
    table_id, title, doc_id, chip_part, page = rows[0]
    assert table_id == "table-3.1"
    assert title == "Table 3.1 Selection of operating modes"
    assert doc_id == "TESTDOC"
    assert chip_part == "RA6M4"
    assert page == 91


def test_build_general_tables_db_disambiguates_duplicate_ids(tmp_path):
    tables_jsonl = tmp_path / "tables.jsonl"
    registry_path = tmp_path / "registry.json"
    db_path = tmp_path / "registers.db"

    # Two untitled tables on the same page collide on the fallback table_id.
    records = [
        {
            "page": 12, "table_idx": 0, "section_path": "§1",
            "is_register": False, "table_title": "",
            "header": ["A", "B"], "rows": [{"A": "1", "B": "2"}],
        },
        {
            "page": 12, "table_idx": 1, "section_path": "§1",
            "is_register": False, "table_title": "",
            "header": ["A", "B"], "rows": [{"A": "3", "B": "4"}],
        },
    ]
    tables_jsonl.write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )
    registry_path.write_text(json.dumps([
        {"doc_id": "TESTDOC", "revision": "1.00", "chip_part": "RA6M4", "path": "x.pdf"}
    ]), encoding="utf-8")

    n = build_general_tables_db(tables_jsonl, registry_path, db_path)
    assert n == 2

    con = sqlite3.connect(str(db_path))
    ids = [r[0] for r in con.execute("SELECT table_id FROM general_tables").fetchall()]
    con.close()
    assert len(set(ids)) == 2
