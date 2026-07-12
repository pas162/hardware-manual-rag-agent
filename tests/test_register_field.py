"""Tests for app/register_tool.py — query_register_field(register_name, bit_or_symbol, chip_part) (R3)."""

import json
import sqlite3

import app.register_tool as register_tool
from app.register_tool import query_register_field


def _setup_db(tmp_path):
    db_path = tmp_path / "registers.db"
    registry_path = tmp_path / "registry.json"

    con = sqlite3.connect(str(db_path))
    con.execute(
        """CREATE TABLE registers (
            peripheral TEXT, register_name TEXT, address TEXT, size_bits INTEGER,
            reset_value TEXT, access TEXT, doc_id TEXT, revision TEXT,
            section_path TEXT, page_start INTEGER, page_end INTEGER, json TEXT,
            PRIMARY KEY (peripheral, register_name)
        )"""
    )
    con.execute(
        """CREATE TABLE bit_fields (
            peripheral TEXT, register_name TEXT, bits TEXT, symbol TEXT,
            access TEXT, reset TEXT, description TEXT
        )"""
    )
    con.execute(
        "INSERT INTO registers VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("ICU", "IELSRn", "0x40006300 + 4*n", 32, "0x00000000", "R/W",
         "TESTDOC", "1.00", "§13.3.2", 284, 284, "{}"),
    )
    con.execute(
        "INSERT INTO bit_fields VALUES (?,?,?,?,?,?,?)",
        ("ICU", "IELSRn", "8:0", "IELS[8:0]", "R/W", "0", "Interrupt select"),
    )
    con.execute(
        "INSERT INTO bit_fields VALUES (?,?,?,?,?,?,?)",
        ("ICU", "IELSRn", "16", "IR", "R/W", "0", "Interrupt request flag"),
    )
    con.commit()
    con.close()

    registry_path.write_text(json.dumps([
        {"doc_id": "TESTDOC", "revision": "1.00", "chip_part": "RA6M4", "path": "x.pdf"}
    ]), encoding="utf-8")

    return db_path, registry_path


def test_query_register_field_by_symbol(tmp_path, monkeypatch):
    db_path, registry_path = _setup_db(tmp_path)
    monkeypatch.setattr(register_tool, "_REGISTRY_PATH", registry_path)

    result = query_register_field("IELSRn", "IR", "RA6M4", db_path=db_path)

    assert result is not None
    assert result["register_name"] == "IELSRn"
    assert result["symbol"] == "IR"
    assert result["bits"] == "16"
    assert result["description"] == "Interrupt request flag"
    assert "TESTDOC" in result["citation"]
    assert "p.284" in result["citation"]


def test_query_register_field_by_exact_bit_index(tmp_path, monkeypatch):
    db_path, registry_path = _setup_db(tmp_path)
    monkeypatch.setattr(register_tool, "_REGISTRY_PATH", registry_path)

    result = query_register_field("IELSRn", "16", "RA6M4", db_path=db_path)

    assert result is not None
    assert result["symbol"] == "IR"


def test_query_register_field_by_bit_within_range(tmp_path, monkeypatch):
    db_path, registry_path = _setup_db(tmp_path)
    monkeypatch.setattr(register_tool, "_REGISTRY_PATH", registry_path)

    result = query_register_field("IELSRn", "4", "RA6M4", db_path=db_path)

    assert result is not None
    assert result["symbol"] == "IELS[8:0]"
    assert result["bits"] == "8:0"


def test_query_register_field_unknown_field_returns_none(tmp_path, monkeypatch):
    db_path, registry_path = _setup_db(tmp_path)
    monkeypatch.setattr(register_tool, "_REGISTRY_PATH", registry_path)

    assert query_register_field("IELSRn", "BOGUS", "RA6M4", db_path=db_path) is None


def test_query_register_field_unknown_register_returns_none(tmp_path, monkeypatch):
    db_path, registry_path = _setup_db(tmp_path)
    monkeypatch.setattr(register_tool, "_REGISTRY_PATH", registry_path)

    assert query_register_field("NOPE", "IR", "RA6M4", db_path=db_path) is None


def test_query_register_field_unknown_chip_part_returns_none(tmp_path, monkeypatch):
    db_path, registry_path = _setup_db(tmp_path)
    monkeypatch.setattr(register_tool, "_REGISTRY_PATH", registry_path)

    assert query_register_field("IELSRn", "IR", "UNKNOWN_CHIP", db_path=db_path) is None
