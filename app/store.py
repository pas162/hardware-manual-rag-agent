"""
Singleton vectorstore and registry cache shared across all app modules.

Load once at startup, reuse everywhere — eliminates duplicate
HuggingFaceEmbeddings + Chroma initialisation in retriever.py and figure_tool.py.
"""

import json
from pathlib import Path

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_chroma import Chroma

_ROOT = Path(__file__).resolve().parent.parent
_COLLECTION_NAME = "hardware_um"
_CHROMA_DIR = _ROOT / "data/store/chroma"
_REGISTRY_PATH = _ROOT / "data/registry.json"
_EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# ── Vectorstore singleton ─────────────────────────────────────────────────────

_vectorstore: Chroma | None = None


def get_vectorstore() -> Chroma:
    """Return the shared Chroma vectorstore, initialising it on first call."""
    global _vectorstore
    if _vectorstore is None:
        embeddings = HuggingFaceEmbeddings(model_name=_EMBED_MODEL)
        _vectorstore = Chroma(
            collection_name=_COLLECTION_NAME,
            embedding_function=embeddings,
            persist_directory=str(_CHROMA_DIR),
        )
    return _vectorstore


# ── Registry cache ────────────────────────────────────────────────────────────

_registry: dict[str, dict] | None = None


def get_registry() -> dict[str, dict]:
    """Return registry dict keyed by chip_part, cached after first read."""
    global _registry
    if _registry is None:
        raw = json.loads(_REGISTRY_PATH.read_text())
        _registry = {r["chip_part"]: r for r in raw}
    return _registry
