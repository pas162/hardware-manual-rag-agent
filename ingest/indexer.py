"""
Embed chunks and persist to ChromaDB.

Reads:  data/parsed/chunks.jsonl
Writes: data/store/chroma/  (persistent Chroma collection)
"""

import json
from pathlib import Path

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma
from langchain_core.documents import Document

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
        "chip_part": chunk["chip_part"],
        "section_title": chunk["section_title"],
        "element_type": chunk["element_type"],
        "figure_id": chunk.get("figure_id", ""),
        "citation": chunk.get("citation", ""),
    }
    return Document(page_content=chunk["render_text"], metadata=metadata)


_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def build_index(
    chunks_jsonl: Path,
    chroma_dir: Path,
    batch_size: int = BATCH_SIZE,
) -> int:
    """Embed all chunks and persist to Chroma. Returns total documents indexed."""
    chroma_dir.mkdir(parents=True, exist_ok=True)

    chunks = load_chunks(chunks_jsonl)
    print(f"Loaded {len(chunks)} chunks, indexing all")

    embeddings = HuggingFaceEmbeddings(model_name=_EMBED_MODEL)

    # Create a single vectorstore instance for all batches
    all_docs = [chunk_to_document(c) for c in chunks]
    total = 0
    vectorstore = None
    for i in range(0, len(all_docs), batch_size):
        batch = all_docs[i : i + batch_size]
        if vectorstore is None:
            vectorstore = Chroma.from_documents(
                documents=batch,
                embedding=embeddings,
                collection_name=COLLECTION_NAME,
                persist_directory=str(chroma_dir),
            )
        else:
            vectorstore.add_documents(batch)
        total += len(batch)
        pct = total / len(all_docs) * 100
        print(f"  Indexed {total}/{len(all_docs)} ({pct:.0f}%)")

    return total


if __name__ == "__main__":
    chunks_jsonl = Path("data/parsed/chunks.jsonl")
    chroma_dir = Path("data/store/chroma")

    print("Embedding and indexing chunks ...")
    n = build_index(chunks_jsonl, chroma_dir)
    print(f"\nIndexed {n} chunks into ChromaDB at {chroma_dir}")

    # Checkpoint
    embeddings = HuggingFaceEmbeddings(model_name=_EMBED_MODEL)
    vectorstore = Chroma(
        collection_name=COLLECTION_NAME,
        embedding_function=embeddings,
        persist_directory=str(chroma_dir),
    )
    results = vectorstore.similarity_search("clock generation circuit", k=3)
    print(f"\nCheckpoint: similarity_search('clock generation circuit', k=3) → {len(results)} results")
    for r in results:
        print(f"  [{r.metadata.get('element_type')}] {r.metadata.get('section_title')}")
        print(f"    {r.page_content[:100]}")
