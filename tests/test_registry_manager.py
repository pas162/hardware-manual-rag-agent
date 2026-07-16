"""Tests for app/registry_manager.py (R6 Part B)."""

import json
import sqlite3

import pytest

import app.registry_manager as registry_manager
from app.registry_manager import (
    add_document,
    detect_gpu_backend,
    get_index_health,
    list_documents,
    remove_document,
    reset_all_data,
)


def _patch_paths(monkeypatch, tmp_path):
    registry_path = tmp_path / "data/registry.json"
    pdfs_dir = tmp_path / "data/pdfs"
    chroma_dir = tmp_path / "data/store/chroma"
    db_path = tmp_path / "data/store/registers.db"
    parsed_dir = tmp_path / "data/parsed"

    registry_path.parent.mkdir(parents=True, exist_ok=True)
    pdfs_dir.mkdir(parents=True, exist_ok=True)
    parsed_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(registry_manager, "_ROOT", tmp_path)
    monkeypatch.setattr(registry_manager, "_REGISTRY_PATH", registry_path)
    monkeypatch.setattr(registry_manager, "_PDFS_DIR", pdfs_dir)
    monkeypatch.setattr(registry_manager, "_CHROMA_DIR", chroma_dir)
    monkeypatch.setattr(registry_manager, "_DB_PATH", db_path)
    monkeypatch.setattr(registry_manager, "_PARSED_DIR", parsed_dir)

    return registry_path, pdfs_dir, chroma_dir, db_path, parsed_dir


def test_list_documents_reports_pdf_exists_and_folder(tmp_path, monkeypatch):
    registry_path, pdfs_dir, chroma_dir, db_path, parsed_dir = _patch_paths(monkeypatch, tmp_path)

    (pdfs_dir / "ra6m4").mkdir()
    (pdfs_dir / "ra6m4" / "manual.pdf").write_bytes(b"%PDF-1.4")

    registry_path.write_text(json.dumps([
        {"doc_id": "DOCA", "revision": "1.00", "chip_part": "RA6M4",
         "path": "data/pdfs/ra6m4/manual.pdf"},
        {"doc_id": "DOCB", "revision": "1.00", "chip_part": "RA6M5",
         "path": "data/pdfs/missing/gone.pdf"},
    ]), encoding="utf-8")

    docs = list_documents()

    by_id = {d["doc_id"]: d for d in docs}
    assert by_id["DOCA"]["pdf_exists"] is True
    assert by_id["DOCA"]["folder"] == "ra6m4"
    assert by_id["DOCB"]["pdf_exists"] is False


def test_add_document_copies_pdf_and_appends_registry_entry(tmp_path, monkeypatch):
    registry_path, pdfs_dir, chroma_dir, db_path, parsed_dir = _patch_paths(monkeypatch, tmp_path)
    registry_path.write_text("[]", encoding="utf-8")

    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4 fake content")

    entry = add_document("DOCA", "1.00", "RA6M4", "ra6m4", str(source_pdf))

    dest = tmp_path / "data/pdfs/ra6m4/source.pdf"
    assert dest.exists()
    assert dest.read_bytes() == b"%PDF-1.4 fake content"
    assert entry["doc_id"] == "DOCA"
    assert entry["path"] == "data/pdfs/ra6m4/source.pdf"

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert len(registry) == 1
    assert registry[0]["doc_id"] == "DOCA"


def test_add_document_rejects_duplicate_doc_id(tmp_path, monkeypatch):
    registry_path, pdfs_dir, chroma_dir, db_path, parsed_dir = _patch_paths(monkeypatch, tmp_path)
    registry_path.write_text(json.dumps([
        {"doc_id": "DOCA", "revision": "1.00", "chip_part": "RA6M4", "path": "data/pdfs/x/a.pdf"}
    ]), encoding="utf-8")

    source_pdf = tmp_path / "source.pdf"
    source_pdf.write_bytes(b"%PDF-1.4")

    with pytest.raises(ValueError):
        add_document("DOCA", "2.00", "RA6M5", "ra6m5", str(source_pdf))


def test_remove_document_without_wipe_keeps_pdf_and_sqlite_rows(tmp_path, monkeypatch):
    registry_path, pdfs_dir, chroma_dir, db_path, parsed_dir = _patch_paths(monkeypatch, tmp_path)
    registry_path.write_text(json.dumps([
        {"doc_id": "DOCA", "revision": "1.00", "chip_part": "RA6M4", "path": "data/pdfs/x/a.pdf"},
        {"doc_id": "DOCB", "revision": "1.00", "chip_part": "RA6M5", "path": "data/pdfs/y/b.pdf"},
    ]), encoding="utf-8")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE registers (peripheral TEXT, register_name TEXT, doc_id TEXT)")
    con.execute("CREATE TABLE bit_fields (peripheral TEXT, register_name TEXT, doc_id TEXT)")
    con.execute("CREATE TABLE general_tables (table_id TEXT, doc_id TEXT)")
    con.execute("INSERT INTO registers VALUES ('P', 'R', 'DOCA')")
    con.commit()
    con.close()

    remove_document("DOCA", wipe_data=False)

    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert [e["doc_id"] for e in registry] == ["DOCB"]

    con = sqlite3.connect(str(db_path))
    count = con.execute("SELECT COUNT(*) FROM registers WHERE doc_id = 'DOCA'").fetchone()[0]
    con.close()
    assert count == 1  # untouched since wipe_data=False


def test_remove_document_with_wipe_deletes_only_that_docs_sqlite_rows(tmp_path, monkeypatch):
    registry_path, pdfs_dir, chroma_dir, db_path, parsed_dir = _patch_paths(monkeypatch, tmp_path)
    registry_path.write_text(json.dumps([
        {"doc_id": "DOCA", "revision": "1.00", "chip_part": "RA6M4", "path": "data/pdfs/x/a.pdf"},
        {"doc_id": "DOCB", "revision": "1.00", "chip_part": "RA6M5", "path": "data/pdfs/y/b.pdf"},
    ]), encoding="utf-8")

    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(db_path))
    con.execute("CREATE TABLE registers (peripheral TEXT, register_name TEXT, doc_id TEXT)")
    con.execute("CREATE TABLE bit_fields (peripheral TEXT, register_name TEXT, doc_id TEXT)")
    con.execute("CREATE TABLE general_tables (table_id TEXT, doc_id TEXT)")
    con.execute("INSERT INTO registers VALUES ('P', 'R', 'DOCA')")
    con.execute("INSERT INTO registers VALUES ('P', 'R', 'DOCB')")
    con.execute("INSERT INTO bit_fields VALUES ('P', 'R', 'DOCA')")
    con.execute("INSERT INTO general_tables VALUES ('T1', 'DOCA')")
    con.commit()
    con.close()

    remove_document("DOCA", wipe_data=True)

    con = sqlite3.connect(str(db_path))
    remaining_registers = con.execute("SELECT doc_id FROM registers").fetchall()
    remaining_bitfields = con.execute("SELECT doc_id FROM bit_fields").fetchall()
    remaining_tables = con.execute("SELECT doc_id FROM general_tables").fetchall()
    con.close()

    assert remaining_registers == [("DOCB",)]
    assert remaining_bitfields == []
    assert remaining_tables == []


def test_reset_all_data_wipes_derived_data_but_keeps_registry_and_pdfs(tmp_path, monkeypatch):
    registry_path, pdfs_dir, chroma_dir, db_path, parsed_dir = _patch_paths(monkeypatch, tmp_path)
    registry_path.write_text(json.dumps([
        {"doc_id": "DOCA", "revision": "1.00", "chip_part": "RA6M4", "path": "data/pdfs/x/a.pdf"}
    ]), encoding="utf-8")

    (pdfs_dir / "x").mkdir(parents=True)
    (pdfs_dir / "x" / "a.pdf").write_bytes(b"%PDF-1.4")

    chroma_dir.mkdir(parents=True)
    (chroma_dir / "some_file").write_text("data", encoding="utf-8")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db_path.write_text("fake db", encoding="utf-8")
    (parsed_dir / "chunks.jsonl").write_text("{}", encoding="utf-8")
    (parsed_dir / "pages.jsonl").write_text("{}", encoding="utf-8")

    reset_all_data()

    assert not chroma_dir.exists()
    assert not db_path.exists()
    assert not (parsed_dir / "chunks.jsonl").exists()
    assert not (parsed_dir / "pages.jsonl").exists()
    assert registry_path.exists()
    assert (pdfs_dir / "x" / "a.pdf").exists()


def test_get_index_health_counts_chunks_by_type(tmp_path, monkeypatch):
    registry_path, pdfs_dir, chroma_dir, db_path, parsed_dir = _patch_paths(monkeypatch, tmp_path)

    chunks = [
        {"element_type": "prose"},
        {"element_type": "prose"},
        {"element_type": "register_row"},
    ]
    (parsed_dir / "chunks.jsonl").write_text(
        "\n".join(json.dumps(c) for c in chunks), encoding="utf-8"
    )

    health = get_index_health()

    assert health["total_chunks"] == 3
    assert health["counts_by_type"] == {"prose": 2, "register_row": 1}


def test_detect_gpu_backend_disables_cuda_regardless_of_torch(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_manager, "_intel_gpu_available", lambda: True)
    monkeypatch.setattr(registry_manager, "_cuda_available", lambda: True)

    result = detect_gpu_backend()

    assert result["intel_gpu"] is True
    assert result["openvino_available"] is True
    assert result["nvidia_gpu"] is True
    assert result["cuda_available"] is False  # always disabled per R6 scope


def test_detect_gpu_backend_no_gpu_present(tmp_path, monkeypatch):
    monkeypatch.setattr(registry_manager, "_intel_gpu_available", lambda: False)
    monkeypatch.setattr(registry_manager, "_cuda_available", lambda: False)

    result = detect_gpu_backend()

    assert result["intel_gpu"] is False
    assert result["openvino_available"] is False
    assert result["nvidia_gpu"] is False
    assert result["cuda_available"] is False
