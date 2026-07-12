"""
One-off benchmark: compare embedding throughput across backends for
sentence-transformers/all-MiniLM-L6-v2 on a sample of real chunks.

Backends: plain PyTorch (CPU, current baseline), OpenVINO CPU, OpenVINO GPU (Iris Xe).

Usage: python scripts/bench_embed.py [n_samples]
"""

import json
import sys
import time
from pathlib import Path

MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
N = int(sys.argv[1]) if len(sys.argv) > 1 else 500


def load_sample_texts(n: int) -> list[str]:
    texts = []
    with Path("data/parsed/chunks.jsonl").open(encoding="utf-8") as f:
        for i, line in enumerate(f):
            if i >= n:
                break
            texts.append(json.loads(line)["render_text"])
    return texts


def bench_pytorch(texts: list[str]) -> float:
    from langchain_huggingface import HuggingFaceEmbeddings

    emb = HuggingFaceEmbeddings(
        model_name=MODEL_ID,
        encode_kwargs={"normalize_embeddings": True},
    )
    start = time.perf_counter()
    emb.embed_documents(texts)
    return time.perf_counter() - start


def bench_openvino(texts: list[str], device: str) -> float:
    from optimum.intel import OVModelForFeatureExtraction
    from transformers import AutoTokenizer
    import torch

    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = OVModelForFeatureExtraction.from_pretrained(
        MODEL_ID, export=True, device=device
    )

    start = time.perf_counter()
    batch_size = 32
    for i in range(0, len(texts), batch_size):
        batch = texts[i : i + batch_size]
        inputs = tokenizer(
            batch, padding=True, truncation=True, max_length=512, return_tensors="pt"
        )
        with torch.no_grad():
            model(**inputs)
    return time.perf_counter() - start


def main():
    texts = load_sample_texts(N)
    print(f"Benchmarking on {len(texts)} real chunks (model={MODEL_ID})\n")

    t_pt = bench_pytorch(texts)
    print(f"PyTorch CPU (baseline) : {t_pt:.1f}s  ({len(texts)/t_pt:.1f} chunks/s)")

    try:
        t_ov_cpu = bench_openvino(texts, "CPU")
        print(f"OpenVINO CPU           : {t_ov_cpu:.1f}s  ({len(texts)/t_ov_cpu:.1f} chunks/s)")
    except Exception as e:
        print(f"OpenVINO CPU           : FAILED ({e})")

    try:
        t_ov_gpu = bench_openvino(texts, "GPU")
        print(f"OpenVINO GPU (Iris Xe) : {t_ov_gpu:.1f}s  ({len(texts)/t_ov_gpu:.1f} chunks/s)")
    except Exception as e:
        print(f"OpenVINO GPU (Iris Xe) : FAILED ({e})")

    print(f"\nProjected time for 21853 chunks (baseline rate): {21853/(len(texts)/t_pt):.0f}s")


if __name__ == "__main__":
    main()
