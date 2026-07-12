"""Tests for app/table_tool.py — get_table(table_id, chip_part) deterministic lookup (R1)."""

import json

from ingest.table_schema import build_general_tables_db
from app.table_tool import get_table


def _setup_db(tmp_path):
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
    ]
    tables_jsonl.write_text("\n".join(json.dumps(r) for r in records), encoding="utf-8")
    registry_path.write_text(json.dumps([
        {"doc_id": "TESTDOC", "revision": "1.00", "chip_part": "RA6M4", "path": "x.pdf"}
    ]), encoding="utf-8")

    build_general_tables_db(tables_jsonl, registry_path, db_path)
    return registry_path, db_path


def test_get_table_returns_markdown_and_citation(tmp_path, monkeypatch):
    registry_path, db_path = _setup_db(tmp_path)
    monkeypatch.setattr("app.table_tool._REGISTRY_PATH", registry_path)

    result = get_table("table-3.1", "RA6M4", db_path=db_path)

    assert result is not None
    assert result["table_id"] == "table-3.1"
    assert result["title"] == "Table 3.1 Selection of operating modes"
    assert "Single-chip mode" in result["markdown"]
    assert result["section_path"] == "§3.3.1"
    assert result["page"] == 91
    assert "TESTDOC" in result["citation"]
    assert "p.91" in result["citation"]


def test_get_table_unknown_table_id_returns_none(tmp_path, monkeypatch):
    registry_path, db_path = _setup_db(tmp_path)
    monkeypatch.setattr("app.table_tool._REGISTRY_PATH", registry_path)

    assert get_table("table-99.9", "RA6M4", db_path=db_path) is None


def test_get_table_unknown_chip_part_returns_none(tmp_path, monkeypatch):
    registry_path, db_path = _setup_db(tmp_path)
    monkeypatch.setattr("app.table_tool._REGISTRY_PATH", registry_path)

    assert get_table("table-3.1", "UNKNOWN_CHIP", db_path=db_path) is None
