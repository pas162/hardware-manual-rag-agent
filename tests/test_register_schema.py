"""Tests for ingest/register_schema.py address/reset/access fallback chain (R5)."""

import json
import sqlite3

from ingest.register_schema import _build_summary_table_index, build_register_db


def _write_jsonl(path, records):
    path.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")


def test_build_summary_table_index_extracts_fields_from_address_map_table(tmp_path):
    tables_jsonl = tmp_path / "tables.jsonl"
    _write_jsonl(tables_jsonl, [
        {
            "page": 1363, "table_idx": 0, "section_path": "§34",
            "is_register": False, "table_title": "Table 34.3 OSPI register configuration",
            "header": ["Register Name", "Symbol", "R/W", "Initial Value", "Address", "Access Size"],
            "rows": [
                {
                    "Register Name": "Device command register", "Symbol": "DCR", "R/W": "R/W",
                    "Initial Value": "0x00000000", "Address": "0x400A_6000", "Access Size": "32",
                },
            ],
        },
    ])

    index = _build_summary_table_index(tables_jsonl)

    assert index["DCR"] == {
        "access": "R/W",
        "reset_value": "0x00000000",
        "address": "0x400A_6000",
    }


def test_build_summary_table_index_skips_register_tables(tmp_path):
    tables_jsonl = tmp_path / "tables.jsonl"
    _write_jsonl(tables_jsonl, [
        {
            "page": 5, "table_idx": 0, "section_path": "§1",
            "is_register": True, "table_title": "",
            "header": ["Bit", "Symbol", "R/W", "Reset", "Description"],
            "rows": [{"Bit": "0", "Symbol": "FOO", "R/W": "R/W", "Reset": "0", "Description": "x"}],
        },
    ])

    index = _build_summary_table_index(tables_jsonl)

    assert index == {}


def test_build_summary_table_index_skips_tables_without_name_or_data_columns(tmp_path):
    tables_jsonl = tmp_path / "tables.jsonl"
    _write_jsonl(tables_jsonl, [
        {
            "page": 10, "table_idx": 0, "section_path": "§2",
            "is_register": False, "table_title": "Table 2.1 Unrelated",
            "header": ["Feature", "Description"],
            "rows": [{"Feature": "x", "Description": "y"}],
        },
    ])

    index = _build_summary_table_index(tables_jsonl)

    assert index == {}


def _base_fixtures(tmp_path, tables, pages_text=""):
    tables_jsonl = tmp_path / "tables.jsonl"
    pages_jsonl = tmp_path / "pages.jsonl"
    registry_path = tmp_path / "registry.json"
    db_path = tmp_path / "registers.db"

    _write_jsonl(tables_jsonl, tables)
    if pages_text:
        pages_jsonl.write_text(pages_text, encoding="utf-8")
    else:
        pages_jsonl.write_text("", encoding="utf-8")
    registry_path.write_text(json.dumps([
        {"doc_id": "TESTDOC", "revision": "1.00", "chip_part": "RA6M4", "path": "x.pdf"}
    ]), encoding="utf-8")

    return tables_jsonl, pages_jsonl, registry_path, db_path


def test_build_register_db_fills_address_from_summary_table_when_prose_is_blank(tmp_path):
    tables_jsonl, pages_jsonl, registry_path, db_path = _base_fixtures(
        tmp_path,
        tables=[
            {
                "doc_id": "TESTDOC", "page": 100, "table_idx": 0, "section_path": "§9",
                "is_register": False, "table_title": "Table 9.1 Register summary",
                "header": ["Register Name", "Address", "Initial Value", "R/W"],
                "rows": [
                    {"Register Name": "DCR", "Address": "0x40100000",
                     "Initial Value": "0x00000000", "R/W": "R/W"},
                ],
            },
            {
                "doc_id": "TESTDOC", "page": 101, "table_idx": 0, "section_path": "§9.1", "register_name": "DCR",
                "peripheral": "OSPI", "is_register": True, "table_title": "",
                "header": ["Bit", "Symbol", "R/W", "Reset", "Description"],
                "rows": [{"Bit": "0", "Symbol": "EN", "R/W": "R/W", "Reset": "0", "Description": "Enable"}],
            },
        ],
        pages_text=json.dumps({
            "doc_id": "TESTDOC", "page": 101, "text": "No address mentioned here.", "section_path": "§9.1",
        }) + "\n",
    )

    build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path)

    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "SELECT address, reset_value, access FROM registers WHERE register_name = 'DCR'"
    ).fetchone()
    con.close()

    assert row == ("0x40100000", "0x00000000", "R/W")


def test_build_register_db_prefers_summary_table_but_falls_back_to_prose_per_field(tmp_path):
    tables_jsonl, pages_jsonl, registry_path, db_path = _base_fixtures(
        tmp_path,
        tables=[
            {
                "doc_id": "TESTDOC", "page": 100, "table_idx": 0, "section_path": "§9",
                "is_register": False, "table_title": "Table 9.1 Register summary",
                "header": ["Register Name", "Address"],
                "rows": [{"Register Name": "DCR", "Address": "0x40100000"}],
            },
            {
                "doc_id": "TESTDOC", "page": 101, "table_idx": 0, "section_path": "§9.1", "register_name": "DCR",
                "peripheral": "OSPI", "is_register": True, "table_title": "",
                "header": ["Bit", "Symbol", "R/W", "Reset", "Description"],
                "rows": [{"Bit": "0", "Symbol": "EN", "R/W": "R/W", "Reset": "0", "Description": "Enable"}],
            },
        ],
        pages_text=json.dumps({
            "doc_id": "TESTDOC",
            "page": 101,
            "text": "DCR Reset Value: 0x00000000 Access: R/W",
            "section_path": "§9.1",
        }) + "\n",
    )

    build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path)

    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "SELECT address, reset_value, access FROM registers WHERE register_name = 'DCR'"
    ).fetchone()
    con.close()

    # address from summary table (no prose match needed); reset_value/access from prose
    # since the summary table row didn't have those columns.
    assert row == ("0x40100000", "0x00000000", "R/W")


def test_build_register_db_defaults_to_empty_string_when_no_source_has_the_field(tmp_path):
    tables_jsonl, pages_jsonl, registry_path, db_path = _base_fixtures(
        tmp_path,
        tables=[
            {
                "doc_id": "TESTDOC", "page": 101, "table_idx": 0, "section_path": "§9.1", "register_name": "DCR",
                "peripheral": "OSPI", "is_register": True, "table_title": "",
                "header": ["Bit", "Symbol", "R/W", "Reset", "Description"],
                "rows": [{"Bit": "0", "Symbol": "EN", "R/W": "R/W", "Reset": "0", "Description": "Enable"}],
            },
        ],
        pages_text=json.dumps({
            "doc_id": "TESTDOC", "page": 101, "text": "No metadata here at all.", "section_path": "§9.1",
        }) + "\n",
    )

    build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path)

    con = sqlite3.connect(str(db_path))
    row = con.execute(
        "SELECT address, reset_value, access FROM registers WHERE register_name = 'DCR'"
    ).fetchone()
    con.close()

    assert row == ("", "", "")
