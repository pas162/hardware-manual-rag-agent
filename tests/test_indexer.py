"""Tests for ingest/indexer.py chunk_to_document() metadata mapping (R4 ref round-trip)."""

from ingest.indexer import chunk_to_document


def _base_chunk(**overrides):
    chunk = {
        "doc_id": "DOC",
        "revision": "1.00",
        "chip_part": "CHIP",
        "section_path": "§1",
        "page_start": 1,
        "page_end": 1,
        "element_type": "prose",
        "render_text": "text",
    }
    chunk.update(overrides)
    return chunk


def test_chunk_to_document_joins_refs_to_comma_separated_strings():
    chunk = _base_chunk(figure_refs=["Figure 13.2", "Figure 13.3"], table_refs=["table-4.1"])

    doc = chunk_to_document(chunk)

    assert doc.metadata["figure_refs"] == "Figure 13.2,Figure 13.3"
    assert doc.metadata["table_refs"] == "table-4.1"


def test_chunk_to_document_empty_refs_default_to_empty_string():
    chunk = _base_chunk()

    doc = chunk_to_document(chunk)

    assert doc.metadata["figure_refs"] == ""
    assert doc.metadata["table_refs"] == ""
