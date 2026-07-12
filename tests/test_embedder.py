"""Tests for ingest/embedder.py — get_embedder() query-prefix + normalization wiring (R2).

HuggingFaceEmbeddings loads the real SentenceTransformer model at construction
time, so these tests patch the class to avoid a real (network/cache-dependent)
model load and only assert on the constructor kwargs get_embedder() passes in.
"""

import ingest.embedder as embedder_module
from ingest.embedder import get_embedder, _query_prefix_for


class _FakeHuggingFaceEmbeddings:
    def __init__(self, **kwargs):
        self.kwargs = kwargs


def test_query_prefix_for_bge_model():
    prefix = _query_prefix_for("BAAI/bge-large-en-v1.5")
    assert prefix == "Represent this sentence for searching relevant passages: "


def test_query_prefix_for_unmatched_model_is_empty():
    assert _query_prefix_for("sentence-transformers/all-MiniLM-L6-v2") == ""


def test_get_embedder_bge_sets_prompt_and_normalizes(monkeypatch):
    monkeypatch.setattr(embedder_module, "HuggingFaceEmbeddings", _FakeHuggingFaceEmbeddings)

    embeddings = get_embedder("BAAI/bge-large-en-v1.5")

    assert embeddings.kwargs["encode_kwargs"] == {"normalize_embeddings": True}
    assert embeddings.kwargs["query_encode_kwargs"]["normalize_embeddings"] is True
    assert embeddings.kwargs["query_encode_kwargs"]["prompt"] == (
        "Represent this sentence for searching relevant passages: "
    )


def test_get_embedder_minilm_normalizes_without_prompt(monkeypatch):
    monkeypatch.setattr(embedder_module, "HuggingFaceEmbeddings", _FakeHuggingFaceEmbeddings)

    embeddings = get_embedder("sentence-transformers/all-MiniLM-L6-v2")

    assert embeddings.kwargs["encode_kwargs"] == {"normalize_embeddings": True}
    assert embeddings.kwargs["query_encode_kwargs"] == {"normalize_embeddings": True}
    assert "prompt" not in embeddings.kwargs["query_encode_kwargs"]
