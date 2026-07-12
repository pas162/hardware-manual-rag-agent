"""Tests for app/retriever.py search() figure_refs/table_refs round-trip (R4)."""

from langchain_core.documents import Document

import app.retriever as retriever_module
from app.retriever import search


class _FakeVectorstore:
    def __init__(self, results):
        self._results = results

    def similarity_search_with_score(self, query, k, filter):
        return self._results


def _patch_registry(monkeypatch):
    monkeypatch.setattr(
        retriever_module,
        "_load_registry",
        lambda: {"RA6M4": {"doc_id": "TESTDOC", "revision": "1.00", "chip_part": "RA6M4"}},
    )


def test_search_splits_refs_back_into_lists(monkeypatch):
    _patch_registry(monkeypatch)
    doc = Document(
        page_content="See Figure 13.2 and Table 4.1.",
        metadata={
            "element_type": "prose",
            "section_path": "§13",
            "page_start": 280,
            "figure_refs": "Figure 13.2",
            "table_refs": "table-4.1",
            "citation": "【TESTDOC Rev.1.00 | §13 | p.280】",
        },
    )
    monkeypatch.setattr(retriever_module, "_get_vectorstore", lambda: _FakeVectorstore([(doc, 0.1)]))

    result = search("interrupt routing", "RA6M4")

    assert isinstance(result, list)
    assert result[0]["figure_refs"] == ["Figure 13.2"]
    assert result[0]["table_refs"] == ["table-4.1"]


def test_search_missing_refs_default_to_empty_list(monkeypatch):
    _patch_registry(monkeypatch)
    doc = Document(
        page_content="No cross-refs here.",
        metadata={
            "element_type": "prose",
            "section_path": "§13",
            "page_start": 280,
            "citation": "【TESTDOC Rev.1.00 | §13 | p.280】",
        },
    )
    monkeypatch.setattr(retriever_module, "_get_vectorstore", lambda: _FakeVectorstore([(doc, 0.1)]))

    result = search("interrupt routing", "RA6M4")

    assert result[0]["figure_refs"] == []
    assert result[0]["table_refs"] == []
