"""
Regression tests for ingest/chunker.py table_summary + table_row chunking (R1).

Reuses the Table 3.1 / Table 4.1 fixtures from tests/test_parser_tables.py so the
chunker is tested against the same real parsed shapes as the parser fix.
"""

import json

from ingest.chunker import _general_table_chunks, _row_to_sentence, _table_summary_text
from ingest.parser_tables import _rows_to_dicts
from tests.test_parser_tables import TABLE_3_1_ROWS, TABLE_4_1_ROWS


def test_table_summary_text_format():
    header = ["Mode-setting pin (MD)", "Operating mode"]
    rows = [{"Mode-setting pin (MD)": "1", "Operating mode": "Single-chip mode"}]
    text = _table_summary_text(header, rows, "Table 3.1 Selection of operating modes", "§3.3.1")

    assert text == (
        "Table 3.1 Selection of operating modes (in §3.3.1) — a table with 1 row(s). "
        "Columns: Mode-setting pin (MD), Operating mode."
    )


def test_row_to_sentence_format():
    header = ["Mode-setting pin (MD)", "Operating mode"]
    row = {"Mode-setting pin (MD)": "1", "Operating mode": "Single-chip mode"}
    text = _row_to_sentence(header, row, "Table 3.1 Selection of operating modes")

    assert text == (
        "In Table 3.1 Selection of operating modes, "
        "Mode-setting pin (MD) is 1, Operating mode is Single-chip mode."
    )


def test_row_to_sentence_skips_empty_cells():
    header = ["A", "B", "C"]
    row = {"A": "1", "B": "", "C": None}
    text = _row_to_sentence(header, row, "T")
    assert text == "In T, A is 1."


def test_row_to_sentence_empty_row_returns_empty_string():
    header = ["A", "B"]
    row = {"A": "", "B": ""}
    assert _row_to_sentence(header, row, "T") == ""


def _write_tables_jsonl(tmp_path, records):
    tables_jsonl = tmp_path / "tables.jsonl"
    tables_jsonl.write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )
    return tables_jsonl


def test_general_table_chunks_table_3_1_emits_summary_and_rows(tmp_path):
    header, rows = _rows_to_dicts(TABLE_3_1_ROWS)
    record = {
        "page": 91, "table_idx": 0, "section_path": "§3.3.1",
        "is_register": False,
        "table_title": "Table 3.1 Selection of operating modes by the mode-setting pin",
        "header": header, "rows": rows,
    }
    tables_jsonl = _write_tables_jsonl(tmp_path, [record])

    chunks = _general_table_chunks(tables_jsonl, "TESTDOC", "1.00", "RA6M4")

    summaries = [c for c in chunks if c["element_type"] == "table_summary"]
    row_chunks = [c for c in chunks if c["element_type"] == "table_row"]

    assert len(summaries) == 1
    assert len(row_chunks) == 2
    assert summaries[0]["table_id"] == "table-3.1"
    assert all(c["table_id"] == "table-3.1" for c in row_chunks)
    assert "Single-chip mode" in row_chunks[0]["render_text"]


def test_general_table_chunks_table_4_1_multilevel_header(tmp_path):
    header, rows = _rows_to_dicts(TABLE_4_1_ROWS)
    record = {
        "page": 93, "table_idx": 0, "section_path": "§4.2",
        "is_register": False,
        "table_title": "Table 4.1 Capacity of the code flash memory, data flash memory, and SRAM0",
        "header": header, "rows": rows,
    }
    tables_jsonl = _write_tables_jsonl(tmp_path, [record])

    chunks = _general_table_chunks(tables_jsonl, "TESTDOC", "1.00", "RA6M4")

    summaries = [c for c in chunks if c["element_type"] == "table_summary"]
    row_chunks = [c for c in chunks if c["element_type"] == "table_row"]

    assert len(summaries) == 1
    assert summaries[0]["table_id"] == "table-4.1"
    assert len(row_chunks) == len(rows)
    assert all(c["table_id"] == "table-4.1" for c in row_chunks)
    assert any("Code flash memory - Capacity is 1 MB" in c["render_text"] for c in row_chunks)


def test_general_table_chunks_skips_register_tables(tmp_path):
    record = {
        "page": 50, "table_idx": 0, "section_path": "§5.1",
        "is_register": True, "table_title": "SCKCR : System Clock Control Register",
        "header": ["Bit", "Symbol", "R/W"],
        "rows": [{"Bit": "0", "Symbol": "CKSEL", "R/W": "R/W"}],
    }
    tables_jsonl = _write_tables_jsonl(tmp_path, [record])

    chunks = _general_table_chunks(tables_jsonl, "TESTDOC", "1.00", "RA6M4")
    assert chunks == []


def test_general_table_chunks_disambiguates_duplicate_table_ids(tmp_path):
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
    tables_jsonl = _write_tables_jsonl(tmp_path, records)

    chunks = _general_table_chunks(tables_jsonl, "TESTDOC", "1.00", "RA6M4")
    summary_ids = [c["table_id"] for c in chunks if c["element_type"] == "table_summary"]
    assert len(summary_ids) == 2
    assert len(set(summary_ids)) == 2
