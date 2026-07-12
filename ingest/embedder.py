"""
Model-agnostic embedder factory with per-model-family query prefix support.

Some embedding models (e.g. the BGE family) are trained to expect a query-side
instruction prefix for asymmetric search, while the document side is left
unprefixed. This module centralizes that behavior so ingest/indexer.py,
app/retriever.py, and app/figure_tool.py all embed consistently regardless of
which model is configured.
"""

from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings

from settings import USE_OPENVINO

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


class OVEmbeddings(Embeddings):
    """OpenVINO-backed embedder (mean-pooled, normalized) for the OpenVINO
    GPU/CPU plugins. On this machine's integrated Iris Xe iGPU, OpenVINO's
    GPU plugin runs roughly 2x faster than PyTorch on CPU for MiniLM-sized
    models (benchmarked in scripts/bench_embed.py).
    """

    def __init__(self, model_name: str, device: str = "GPU"):
        from optimum.intel import OVModelForFeatureExtraction
        from transformers import AutoTokenizer

        self.tokenizer = AutoTokenizer.from_pretrained(model_name)
        self.model = OVModelForFeatureExtraction.from_pretrained(
            model_name, export=True, device=device
        )
        self.query_prefix = _query_prefix_for(model_name)

    def _encode(self, texts: list[str]) -> list[list[float]]:
        import torch

        inputs = self.tokenizer(
            texts, padding=True, truncation=True, max_length=512, return_tensors="pt"
        )
        with torch.no_grad():
            output = self.model(**inputs)
        token_embeddings = output.last_hidden_state
        mask = inputs["attention_mask"].unsqueeze(-1).expand(token_embeddings.size()).float()
        summed = torch.sum(token_embeddings * mask, dim=1)
        counts = torch.clamp(mask.sum(dim=1), min=1e-9)
        pooled = summed / counts
        normalized = torch.nn.functional.normalize(pooled, p=2, dim=1)
        return normalized.numpy().tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._encode(texts)

    def embed_query(self, text: str) -> list[float]:
        text = self.query_prefix + text if self.query_prefix else text
        return self._encode([text])[0]


def _openvino_gpu_available() -> bool:
    try:
        import openvino as ov

        return "GPU" in ov.Core().available_devices
    except Exception:
        return False


def get_embedder(model_name: str) -> Embeddings:
    """Build an embedder with normalized vectors and the correct query-side
    prefix for the given model family.

    Normalization is required for app/retriever.py's cosine-similarity-derived
    L2 threshold guard to hold.

    Uses the OpenVINO GPU backend when USE_OPENVINO=1 in .env and an
    OpenVINO-visible GPU is present; falls back to plain PyTorch (CPU)
    otherwise, so this is safe to leave on for machines without a
    compatible GPU.
    """
    if USE_OPENVINO and _openvino_gpu_available():
        return OVEmbeddings(model_name, device="GPU")

    query_encode_kwargs = {"normalize_embeddings": True}
    prefix = _query_prefix_for(model_name)
    if prefix:
        query_encode_kwargs["prompt"] = prefix

    return HuggingFaceEmbeddings(
        model_name=model_name,
        encode_kwargs={"normalize_embeddings": True},
        query_encode_kwargs=query_encode_kwargs,
    )
