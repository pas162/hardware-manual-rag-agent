"""Multi-document regression tests (R6 Part A).

Two documents sharing the same (peripheral, register_name) pair must not
collide or contaminate each other across registers.db, bit_fields, general_tables,
or chunks.jsonl. This is the scenario that motivated adding doc_id to bit_fields
and (peripheral, register_name, doc_id) as the registers primary key.
"""

import json
import sqlite3

from ingest.chunker import build_chunks
from ingest.register_schema import build_register_db
from ingest.table_schema import build_general_tables_db


def _write_jsonl(path, records):
    path.write_text(
        "\n".join(json.dumps(r) for r in records), encoding="utf-8"
    )


DOC_A = {"doc_id": "DOCA", "revision": "1.00", "chip_part": "CHIPA", "path": "a.pdf"}
DOC_B = {"doc_id": "DOCB", "revision": "2.00", "chip_part": "CHIPB", "path": "b.pdf"}

# Both documents define a register named SCKCR on peripheral CLK, but with
# different bit-field contents — a real collision if doc_id weren't threaded
# through the schema.
_REGISTER_TABLE_A = {
    "doc_id": "DOCA", "page": 10, "table_idx": 0, "section_path": "§5.1",
    "register_name": "SCKCR", "peripheral": "CLK", "is_register": True, "table_title": "",
    "header": ["Bit", "Symbol", "R/W", "Reset", "Description"],
    "rows": [{"Bit": "0", "Symbol": "CKSEL_A", "R/W": "R/W", "Reset": "0", "Description": "Doc A field"}],
}
_REGISTER_TABLE_B = {
    "doc_id": "DOCB", "page": 20, "table_idx": 0, "section_path": "§7.2",
    "register_name": "SCKCR", "peripheral": "CLK", "is_register": True, "table_title": "",
    "header": ["Bit", "Symbol", "R/W", "Reset", "Description"],
    "rows": [{"Bit": "1", "Symbol": "CKSEL_B", "R/W": "R/W", "Reset": "1", "Description": "Doc B field"}],
}

# Both documents also each have a general (non-register) table that collides
# on the derived table_id (no title, same page/idx) unless scoped per-doc_id.
_GENERAL_TABLE_A = {
    "doc_id": "DOCA", "page": 1, "table_idx": 0, "section_path": "§1",
    "is_register": False, "table_title": "",
    "header": ["A", "B"], "rows": [{"A": "a1", "B": "a2"}],
}
_GENERAL_TABLE_B = {
    "doc_id": "DOCB", "page": 1, "table_idx": 0, "section_path": "§1",
    "is_register": False, "table_title": "",
    "header": ["A", "B"], "rows": [{"A": "b1", "B": "b2"}],
}


def _setup(tmp_path):
    tables_jsonl = tmp_path / "tables.jsonl"
    pages_jsonl = tmp_path / "pages.jsonl"
    figures_jsonl = tmp_path / "figures.jsonl"
    registry_path = tmp_path / "registry.json"
    db_path = tmp_path / "registers.db"

    _write_jsonl(tables_jsonl, [
        _REGISTER_TABLE_A, _REGISTER_TABLE_B, _GENERAL_TABLE_A, _GENERAL_TABLE_B,
    ])
    pages_jsonl.write_text("", encoding="utf-8")
    figures_jsonl.write_text("", encoding="utf-8")
    registry_path.write_text(json.dumps([DOC_A, DOC_B]), encoding="utf-8")

    return tables_jsonl, pages_jsonl, figures_jsonl, registry_path, db_path


def test_build_register_db_keeps_same_named_registers_from_different_docs_separate(tmp_path):
    tables_jsonl, pages_jsonl, figures_jsonl, registry_path, db_path = _setup(tmp_path)

    n = build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path)
    assert n == 2

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT doc_id, peripheral, register_name FROM registers "
        "WHERE peripheral = 'CLK' AND register_name = 'SCKCR' ORDER BY doc_id"
    ).fetchall()
    con.close()

    assert len(rows) == 2
    assert {r["doc_id"] for r in rows} == {"DOCA", "DOCB"}


def test_build_register_db_bit_fields_not_cross_contaminated(tmp_path):
    tables_jsonl, pages_jsonl, figures_jsonl, registry_path, db_path = _setup(tmp_path)

    build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path)

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row

    bf_a = con.execute(
        "SELECT symbol FROM bit_fields WHERE peripheral = 'CLK' AND register_name = 'SCKCR' AND doc_id = 'DOCA'"
    ).fetchall()
    bf_b = con.execute(
        "SELECT symbol FROM bit_fields WHERE peripheral = 'CLK' AND register_name = 'SCKCR' AND doc_id = 'DOCB'"
    ).fetchall()
    con.close()

    assert [r["symbol"] for r in bf_a] == ["CKSEL_A"]
    assert [r["symbol"] for r in bf_b] == ["CKSEL_B"]


def test_build_general_tables_db_disambiguates_across_documents(tmp_path):
    tables_jsonl, pages_jsonl, figures_jsonl, registry_path, db_path = _setup(tmp_path)

    n = build_general_tables_db(tables_jsonl, registry_path, db_path)
    assert n == 2

    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    rows = con.execute(
        "SELECT doc_id, table_id, markdown FROM general_tables ORDER BY doc_id"
    ).fetchall()
    con.close()

    assert len(rows) == 2
    assert rows[0]["doc_id"] == "DOCA" and "a1" in rows[0]["markdown"]
    assert rows[1]["doc_id"] == "DOCB" and "b1" in rows[1]["markdown"]


def test_build_chunks_register_row_and_table_chunks_not_cross_contaminated(tmp_path):
    tables_jsonl, pages_jsonl, figures_jsonl, registry_path, db_path = _setup(tmp_path)

    build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path)
    build_general_tables_db(tables_jsonl, registry_path, db_path)

    output_path = tmp_path / "chunks.jsonl"
    counts = build_chunks(
        pages_jsonl, figures_jsonl, tables_jsonl, db_path, registry_path, output_path,
    )
    assert counts["register_row"] == 2

    chunks = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]

    register_chunks = [c for c in chunks if c["element_type"] == "register_row"]
    by_doc = {c["doc_id"]: c for c in register_chunks}
    assert "CKSEL_A" in by_doc["DOCA"]["render_text"]
    assert "CKSEL_B" not in by_doc["DOCA"]["render_text"]
    assert "CKSEL_B" in by_doc["DOCB"]["render_text"]
    assert "CKSEL_A" not in by_doc["DOCB"]["render_text"]

    table_summaries = [c for c in chunks if c["element_type"] == "table_summary"]
    assert len(table_summaries) == 2
    assert len({c["table_id"] for c in table_summaries}) == 2 or (
        len(table_summaries) == 2 and table_summaries[0]["doc_id"] != table_summaries[1]["doc_id"]
    )

    table_rows = [c for c in chunks if c["element_type"] == "table_row"]
    by_doc_rows = {c["doc_id"]: c["render_text"] for c in table_rows}
    assert "a1" in by_doc_rows["DOCA"]
    assert "b1" in by_doc_rows["DOCB"]


def test_build_register_db_only_doc_id_processes_single_document(tmp_path):
    tables_jsonl, pages_jsonl, figures_jsonl, registry_path, db_path = _setup(tmp_path)

    n = build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path, only_doc_id="DOCA")
    assert n == 1

    con = sqlite3.connect(str(db_path))
    rows = con.execute("SELECT doc_id FROM registers").fetchall()
    con.close()

    assert [r[0] for r in rows] == ["DOCA"]


def test_build_chunks_only_doc_id_preserves_other_documents_chunks(tmp_path):
    tables_jsonl, pages_jsonl, figures_jsonl, registry_path, db_path = _setup(tmp_path)

    build_register_db(tables_jsonl, pages_jsonl, registry_path, db_path)
    build_general_tables_db(tables_jsonl, registry_path, db_path)

    output_path = tmp_path / "chunks.jsonl"
    build_chunks(pages_jsonl, figures_jsonl, tables_jsonl, db_path, registry_path, output_path)

    all_chunks_before = output_path.read_text(encoding="utf-8").splitlines()
    doc_b_chunks_before = [
        json.loads(line) for line in all_chunks_before if json.loads(line)["doc_id"] == "DOCB"
    ]

    # Re-run for DOCA only — DOCB's chunks must survive untouched. counts is
    # summed across the whole output file, so it's DOCB's preserved chunk plus
    # DOCA's freshly rebuilt one.
    counts = build_chunks(
        pages_jsonl, figures_jsonl, tables_jsonl, db_path, registry_path, output_path,
        only_doc_id="DOCA",
    )
    assert counts["register_row"] == 2

    all_chunks_after = [json.loads(line) for line in output_path.read_text(encoding="utf-8").splitlines()]
    doc_b_chunks_after = [c for c in all_chunks_after if c["doc_id"] == "DOCB"]
    doc_a_chunks_after = [c for c in all_chunks_after if c["doc_id"] == "DOCA"]

    assert len(doc_b_chunks_after) == len(doc_b_chunks_before)
    assert all(c in doc_b_chunks_before for c in doc_b_chunks_after)
    assert len(doc_a_chunks_after) > 0
