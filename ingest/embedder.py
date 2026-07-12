"""
Model-agnostic embedder factory with per-model-family query prefix support.

Some embedding models (e.g. the BGE family) are trained to expect a query-side
instruction prefix for asymmetric search, while the document side is left
unprefixed. This module centralizes that behavior so ingest/indexer.py,
app/retriever.py, and app/figure_tool.py all embed consistently regardless of
which model is configured.
"""

from langchain_huggingface import HuggingFaceEmbeddings

# Keyed by a lowercase substring of the model name. Unmatched models get no
# query prefix, preserving symmetric-embedding behavior (e.g. MiniLM).
_QUERY_PREFIXES = {
    "bge": "Represent this sentence for searching relevant passages: ",
}


def _query_prefix_for(model_name: str) -> str:
    name_lower = model_name.lower()
    for key, prefix in _QUERY_PREFIXES.items():
        if key in name_lower:
            return prefix
    return ""


def get_embedder(model_name: str) -> HuggingFaceEmbeddings:
    """Build a HuggingFaceEmbeddings instance with normalized vectors and the
    correct query-side prefix for the given model family.

    Normalization is required for app/retriever.py's cosine-similarity-derived
    L2 threshold guard to hold.
    """
    query_encode_kwargs = {"normalize_embeddings": True}
    prefix = _query_prefix_for(model_name)
    if prefix:
        query_encode_kwargs["prompt"] = prefix

    return HuggingFaceEmbeddings(
        model_name=model_name,
        encode_kwargs={"normalize_embeddings": True},
        query_encode_kwargs=query_encode_kwargs,
    )
