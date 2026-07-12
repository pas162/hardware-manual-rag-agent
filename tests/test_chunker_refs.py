"""Tests for ingest/chunker.py cross-reference extraction (R4)."""

from ingest.chunker import _extract_refs, _prose_chunks


def test_extract_single_figure_ref():
    figure_refs, table_refs = _extract_refs("See Figure 13.2 for the block diagram.")
    assert figure_refs == ["Figure 13.2"]
    assert table_refs == []


def test_extract_single_table_ref():
    figure_refs, table_refs = _extract_refs("Refer to Table 4.1 for capacity values.")
    assert figure_refs == []
    assert table_refs == ["table-4.1"]


def test_extract_both_figure_and_table_refs():
    figure_refs, table_refs = _extract_refs(
        "As shown in Figure 8.1 and summarized in Table 3.1, the clock tree ..."
    )
    assert figure_refs == ["Figure 8.1"]
    assert table_refs == ["table-3.1"]


def test_extract_multiple_distinct_refs_deduplicated():
    text = "See Figure 13.2. Also see Figure 13.3. Figure 13.2 is referenced again."
    figure_refs, _ = _extract_refs(text)
    assert figure_refs == ["Figure 13.2", "Figure 13.3"]


def test_extract_no_refs_returns_empty_lists():
    figure_refs, table_refs = _extract_refs("No cross references here.")
    assert figure_refs == []
    assert table_refs == []


def test_extract_case_insensitive():
    figure_refs, table_refs = _extract_refs("see fig. 2.1 and table 5.3")
    assert figure_refs == ["Figure 2.1"]
    assert table_refs == ["table-5.3"]


def test_prose_chunks_include_ref_fields(tmp_path):
    pages_jsonl = tmp_path / "pages.jsonl"
    pages_jsonl.write_text(
        '{"section_path": "§13 > §13.1", "page": 280, "bbox": [0, 0, 0, 0], '
        '"text": "See Figure 13.2 and Table 4.1 for details."}\n',
        encoding="utf-8",
    )

    chunks = _prose_chunks(pages_jsonl, "DOC", "1.00", "CHIP")

    assert len(chunks) == 1
    assert chunks[0]["figure_refs"] == ["Figure 13.2"]
    assert chunks[0]["table_refs"] == ["table-4.1"]
