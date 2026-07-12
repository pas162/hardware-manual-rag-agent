"""
Chroma-backed semantic retriever with similarity threshold guard and citation attachment.
"""

import json
from pathlib import Path

from langchain_chroma import Chroma

from ingest.embedder import get_embedder
from settings import EMBED_MODEL

_ROOT = Path(__file__).resolve().parent.parent
_COLLECTION_NAME = "hardware_um"
_CHROMA_DIR = _ROOT / "data/store/chroma"
_REGISTRY_PATH = _ROOT / "data/registry.json"
_SIMILARITY_THRESHOLD = 0.30
_DEFAULT_K = 6
_MAX_K = 10

_vectorstore: Chroma | None = None


def _get_vectorstore() -> Chroma:
    global _vectorstore
    if _vectorstore is None:
        embeddings = get_embedder(EMBED_MODEL)
        _vectorstore = Chroma(
            collection_name=_COLLECTION_NAME,
            embedding_function=embeddings,
            persist_directory=str(_CHROMA_DIR),
        )
    return _vectorstore


def _load_registry() -> dict[str, dict]:
    registry = json.loads(_REGISTRY_PATH.read_text())
    return {r["chip_part"]: r for r in registry}


def search(query: str, chip_part: str, top_k: int = _DEFAULT_K) -> list[dict] | str:
    """Semantic search over the UM vector store.

    Returns a list of chunk dicts, or a refusal string when the top similarity
    score is below the threshold.
    """
    top_k = min(top_k, _MAX_K)
    registry = _load_registry()
    if chip_part not in registry:
        doc_info = next(iter(registry.values()))
    else:
        doc_info = registry[chip_part]

    revision = doc_info["revision"]

    vs = _get_vectorstore()
    # similarity_search_with_score returns (Document, score) pairs
    # Chroma cosine distance: lower = more similar (0 = identical)
    results_with_score = vs.similarity_search_with_score(
        query,
        k=top_k,
        filter={"chip_part": chip_part},
    )

    if not results_with_score:
        return f"No relevant content found in {chip_part} UM Rev.{revision}."

    # Chroma returns L2 distance by default; for unit-normalized embeddings this
    # is exactly 2*(1-cos_sim) — holds because get_embedder() sets normalize_embeddings=True.
    # Lower score = more similar. Threshold applied as: if min_score > threshold → refusal.
    # We treat "score" here as distance; the guard fires when the BEST result is too far.
    best_score = results_with_score[0][1]
    if best_score > (2 * (1 - _SIMILARITY_THRESHOLD)):  # convert cosine sim threshold to L2
        return f"No relevant content found in {chip_part} UM Rev.{revision}."

    chunks = []
    for doc, _score in results_with_score:
        meta = doc.metadata
        chunks.append({
            "element_type": meta.get("element_type", ""),
            "section_path": meta.get("section_path", ""),
            "page": meta.get("page_start", 0),
            "render_text": doc.page_content,
            "peripheral": meta.get("peripheral", ""),
            "register_name": meta.get("register_name", ""),
            "figure_id": meta.get("figure_id", ""),
            "image_path": meta.get("image_path", ""),
            "citation": meta.get("citation", ""),
            "score": round(float(_score), 4),
        })

    return chunks


if __name__ == "__main__":
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "clock generation circuit"
    chip = sys.argv[2] if len(sys.argv) > 2 else "RA6M4"

    result = search(query, chip)
    if isinstance(result, str):
        print(f"Refusal: {result}")
    else:
        for c in result:
            print(f"  [{c['element_type']}] {c['section_path']} p{c['page']}  score={c['score']}")
            print(f"    {c['render_text'][:100]}")
            print(f"    {c['citation']}")
