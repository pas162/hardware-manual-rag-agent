"""
Embed chunks and persist to ChromaDB.

Reads:  data/parsed/chunks.jsonl
Writes: data/store/chroma/  (persistent Chroma collection)
"""

import json
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

from ingest.embedder import get_embedder
from settings import EMBED_MODEL

COLLECTION_NAME = "hardware_um"
BATCH_SIZE = 100


def load_chunks(chunks_jsonl: Path) -> list[dict]:
    chunks = []
    with chunks_jsonl.open(encoding="utf-8") as f:
        for line in f:
            chunks.append(json.loads(line))
    return chunks


def chunk_to_document(chunk: dict) -> Document:
    metadata = {
        "doc_id": chunk["doc_id"],
        "revision": chunk["revision"],
        "chip_part": chunk["chip_part"],
        "section_path": chunk["section_path"],
        "page_start": chunk["page_start"],
        "page_end": chunk["page_end"],
        "element_type": chunk["element_type"],
        "peripheral": chunk.get("peripheral", ""),
        "register_name": chunk.get("register_name", ""),
        "figure_id": chunk.get("figure_id", ""),
        "image_path": chunk.get("image_path", ""),
        "table_id": chunk.get("table_id", ""),
        "citation": chunk.get("citation", ""),
        "figure_refs": ",".join(chunk.get("figure_refs", [])),
        "table_refs": ",".join(chunk.get("table_refs", [])),
    }
    return Document(page_content=chunk["render_text"], metadata=metadata)


def build_index(
    chunks_jsonl: Path,
    chroma_dir: Path,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Embed all chunks and persist to Chroma. Returns total documents indexed."""
    chroma_dir.mkdir(parents=True, exist_ok=True)

    chunks = load_chunks(chunks_jsonl)
    print(f"Loaded {len(chunks)} chunks")

    embeddings = get_embedder(EMBED_MODEL)

    # Build in batches
    total = 0
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i : i + batch_size]
        docs = [chunk_to_document(c) for c in batch]

        if i == 0:
            # Create collection on first batch
            vectorstore = Chroma.from_documents(
                documents=docs,
                embedding=embeddings,
                collection_name=COLLECTION_NAME,
                persist_directory=str(chroma_dir),
            )
        else:
            vectorstore = Chroma(
                collection_name=COLLECTION_NAME,
                embedding_function=embeddings,
                persist_directory=str(chroma_dir),
            )
            vectorstore.add_documents(docs)

        total += len(batch)
        pct = total / len(chunks) * 100
        print(f"  Indexed {total}/{len(chunks)} ({pct:.0f}%)")

    return total


if __name__ == "__main__":
    chunks_jsonl = Path("data/parsed/chunks.jsonl")
    chroma_dir = Path("data/store/chroma")

    print("Embedding and indexing chunks ...")
    n = build_index(chunks_jsonl, chroma_dir)
    print(f"\nIndexed {n} chunks into ChromaDB at {chroma_dir}")

    # Checkpoint
    embeddings = get_embedder(EMBED_MODEL)
    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(chroma_dir),
    )
    results = vectorstore.similarity_search("clock generation circuit", k=3)
    print(f"\nCheckpoint: similarity_search('clock generation circuit', k=3) → {len(results)} results")
    for r in results:
        print(f"  [{r.metadata.get('element_type')}] {r.metadata.get('section_path')} p{r.metadata.get('page_start')}")
        print(f"    {r.page_content[:100]}")
