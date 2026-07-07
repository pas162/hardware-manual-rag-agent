"""
Hybrid retriever: dense (Chroma) + sparse (BM25) with Reciprocal Rank Fusion.

Dense search finds semantically similar chunks; BM25 boosts exact keyword matches.
RRF merges both ranked lists without requiring score normalisation.
"""

import json
from pathlib import Path

from rank_bm25 import BM25Okapi

from app.store import get_vectorstore

_ROOT = Path(__file__).resolve().parent.parent
_CHUNKS_JSONL = _ROOT / "data/parsed/chunks.jsonl"
_SIMILARITY_THRESHOLD = 0.30
_DEFAULT_K = 6
_MAX_K = 10
_CANDIDATE_K = 20   # candidates fetched from each retriever before RRF merge
_RRF_K = 60         # RRF constant — higher = gentler rank penalty

# ── BM25 index (built once per process) ──────────────────────────────────────

_bm25: BM25Okapi | None = None
_bm25_docs: list[dict] | None = None   # parallel list of chunk dicts


def _get_bm25(chip_part: str) -> tuple["BM25Okapi", list[dict], list[int]]:
    """Return (BM25 index, ordered chunk list) for the given chip_part, built lazily."""
    global _bm25, _bm25_docs
    if _bm25 is None:
        chunks = []
        with _CHUNKS_JSONL.open(encoding="utf-8") as f:
            for line in f:
                chunks.append(json.loads(line))

        tokenised = [c["render_text"].lower().split() for c in chunks]
        _bm25 = BM25Okapi(tokenised)
        _bm25_docs = chunks

    # Filter to chip_part at query time (cheap — just index lookup)
    indices = [i for i, c in enumerate(_bm25_docs) if c.get("chip_part") == chip_part]
    return _bm25, _bm25_docs, indices


def _rrf_merge(
    dense_ids: list[str],
    bm25_ids: list[str],
    id_to_chunk: dict[str, dict],
    top_k: int,
) -> list[dict]:
    """Merge two ranked ID lists with Reciprocal Rank Fusion."""
    scores: dict[str, float] = {}
    for rank, cid in enumerate(dense_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)
    for rank, cid in enumerate(bm25_ids):
        scores[cid] = scores.get(cid, 0.0) + 1.0 / (_RRF_K + rank + 1)

    ranked = sorted(scores, key=lambda x: scores[x], reverse=True)
    result = []
    for cid in ranked[:top_k]:
        chunk = id_to_chunk.get(cid)
        if chunk:
            chunk["score"] = round(scores[cid], 4)
            result.append(chunk)
    return result


def search(query: str, chip_part: str, top_k: int = _DEFAULT_K) -> list[dict] | str:
    """Hybrid BM25 + dense search over the UM vector store.

    Returns a list of chunk dicts, or a refusal string when no good match found.
    """
    top_k = min(top_k, _MAX_K)
    candidate_k = max(_CANDIDATE_K, top_k * 3)

    # ── Dense retrieval ───────────────────────────────────────────────────────
    vs = get_vectorstore()
    dense_results = vs.similarity_search_with_score(
        query,
        k=candidate_k,
        filter={"chip_part": chip_part},
    )

    if not dense_results:
        return f"No relevant content found in {chip_part} Smart Manual."

    best_score = dense_results[0][1]
    if best_score > (2 * (1 - _SIMILARITY_THRESHOLD)):
        return f"No relevant content found in {chip_part} Smart Manual."

    # Build id→chunk map from dense results
    id_to_chunk: dict[str, dict] = {}
    dense_ids: list[str] = []
    for doc, _score in dense_results:
        meta = doc.metadata
        # Use citation as stable ID (unique per chunk)
        cid = meta.get("citation", "") or doc.page_content[:80]
        if cid not in id_to_chunk:
            id_to_chunk[cid] = {
                "element_type": meta.get("element_type", ""),
                "section_title": meta.get("section_title", ""),
                "render_text": doc.page_content,
                "figure_id": meta.get("figure_id", ""),
                "citation": meta.get("citation", ""),
            }
        dense_ids.append(cid)

    # ── BM25 retrieval ────────────────────────────────────────────────────────
    bm25_index, bm25_docs, chip_indices = _get_bm25(chip_part)
    tokens = query.lower().split()
    all_scores = bm25_index.get_scores(tokens)

    # Score only chunks belonging to this chip_part, take top candidates
    scored = sorted(chip_indices, key=lambda i: all_scores[i], reverse=True)[:candidate_k]

    bm25_ids: list[str] = []
    for i in scored:
        c = bm25_docs[i]
        cid = c.get("citation", "") or c["render_text"][:80]
        if cid not in id_to_chunk:
            id_to_chunk[cid] = {
                "element_type": c.get("element_type", ""),
                "section_title": c.get("section_title", ""),
                "render_text": c["render_text"],
                "figure_id": c.get("figure_id", ""),
                "citation": c.get("citation", ""),
            }
        bm25_ids.append(cid)

    # ── RRF merge ─────────────────────────────────────────────────────────────
    return _rrf_merge(dense_ids, bm25_ids, id_to_chunk, top_k)


if __name__ == "__main__":
    import sys

    query = sys.argv[1] if len(sys.argv) > 1 else "clock generation circuit"
    chip = sys.argv[2] if len(sys.argv) > 2 else "RA6M4"

    result = search(query, chip)
    if isinstance(result, str):
        print(f"Refusal: {result}")
    else:
        for c in result:
            print(f"  [{c['element_type']}] {c['section_title']}  score={c['score']}")
            print(f"    {c['render_text'][:100]}")
            print(f"    {c['citation']}")