"""
Backend for the Settings UI (app/settings_ui.py) — pure Python, no Gradio
dependency, so it's directly unit-testable.

Manages data/registry.json, the PDFs under data/pdfs/<folder>/, and the
derived index (Chroma + SQLite), plus hardware-acceleration detection for the
advanced settings panel.
"""

import json
import shutil
import sqlite3
from collections.abc import Iterator
from pathlib import Path

from dotenv import set_key

from ingest.run_all import run_pipeline

_ROOT = Path(__file__).resolve().parent.parent
_REGISTRY_PATH = _ROOT / "data/registry.json"
_PDFS_DIR = _ROOT / "data/pdfs"
_CHROMA_DIR = _ROOT / "data/store/chroma"
_DB_PATH = _ROOT / "data/store/registers.db"
_PARSED_DIR = _ROOT / "data/parsed"
_ENV_PATH = _ROOT / ".env"
_COLLECTION_NAME = "hardware_um"


# ── Registry read/write ─────────────────────────────────────────────────────

def _load_registry() -> list[dict]:
    if not _REGISTRY_PATH.exists():
        return []
    return json.loads(_REGISTRY_PATH.read_text(encoding="utf-8"))


def _save_registry(registry: list[dict]) -> None:
    _REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    _REGISTRY_PATH.write_text(json.dumps(registry, indent=2), encoding="utf-8")


def _chunk_count_for_doc(doc_id: str) -> int:
    if not _CHROMA_DIR.exists():
        return 0
    try:
        import chromadb

        client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
        collection = client.get_collection(_COLLECTION_NAME)
        result = collection.get(where={"doc_id": doc_id})
        return len(result["ids"])
    except Exception:
        return 0


def _register_count_for_doc(doc_id: str) -> int:
    if not _DB_PATH.exists():
        return 0
    con = sqlite3.connect(str(_DB_PATH))
    try:
        row = con.execute(
            "SELECT COUNT(*) FROM registers WHERE doc_id = ?", (doc_id,)
        ).fetchone()
        return row[0] if row else 0
    except sqlite3.OperationalError:
        return 0
    finally:
        con.close()


def list_documents() -> list[dict]:
    """List every registry entry with computed status fields.

    Each entry gains: pdf_exists, folder, chunk_count, register_count.
    """
    registry = _load_registry()
    docs = []
    for entry in registry:
        pdf_path = _ROOT / entry["path"]
        try:
            folder = pdf_path.relative_to(_PDFS_DIR).parent.as_posix()
        except ValueError:
            folder = ""
        docs.append({
            **entry,
            "pdf_exists": pdf_path.exists(),
            "folder": folder,
            "chunk_count": _chunk_count_for_doc(entry["doc_id"]),
            "register_count": _register_count_for_doc(entry["doc_id"]),
        })
    return docs


def add_document(
    doc_id: str, revision: str, chip_part: str, folder: str, pdf_source_path: str
) -> dict:
    """Copy pdf_source_path into data/pdfs/<folder>/<filename> and register it.

    Raises ValueError if doc_id already exists in the registry.
    """
    registry = _load_registry()
    if any(e["doc_id"] == doc_id for e in registry):
        raise ValueError(f"doc_id {doc_id!r} already exists in the registry")

    source = Path(pdf_source_path)
    dest_dir = _PDFS_DIR / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest_path = dest_dir / source.name
    shutil.copyfile(source, dest_path)

    entry = {
        "doc_id": doc_id,
        "revision": revision,
        "chip_part": chip_part,
        "path": dest_path.relative_to(_ROOT).as_posix(),
    }
    registry.append(entry)
    _save_registry(registry)
    return entry


def remove_document(doc_id: str, wipe_data: bool = False) -> None:
    """Remove doc_id from the active registry. Does not delete the PDF file.

    When wipe_data is True, also deletes that document's Chroma rows and
    SQLite rows (registers, bit_fields, general_tables) — scoped strictly to
    this doc_id, leaving every other document's data untouched.
    """
    registry = _load_registry()
    registry = [e for e in registry if e["doc_id"] != doc_id]
    _save_registry(registry)

    if not wipe_data:
        return

    if _CHROMA_DIR.exists():
        try:
            import chromadb

            client = chromadb.PersistentClient(path=str(_CHROMA_DIR))
            collection = client.get_collection(_COLLECTION_NAME)
            collection.delete(where={"doc_id": doc_id})
        except Exception:
            pass

    if _DB_PATH.exists():
        con = sqlite3.connect(str(_DB_PATH))
        try:
            con.execute("DELETE FROM bit_fields WHERE doc_id = ?", (doc_id,))
            con.execute("DELETE FROM registers WHERE doc_id = ?", (doc_id,))
            con.execute("DELETE FROM general_tables WHERE doc_id = ?", (doc_id,))
            con.commit()
        except sqlite3.OperationalError:
            pass
        finally:
            con.close()


def reset_all_data() -> None:
    """Wipe the entire derived index (Chroma, SQLite, parsed intermediates).

    Leaves registry.json and the PDFs themselves untouched, so the next
    ingest run starts clean for every document currently registered.
    """
    if _CHROMA_DIR.exists():
        shutil.rmtree(_CHROMA_DIR)
    if _DB_PATH.exists():
        _DB_PATH.unlink()
    if _PARSED_DIR.exists():
        for f in _PARSED_DIR.glob("*.jsonl"):
            f.unlink()


def trigger_ingest(doc_id: str) -> Iterator[str]:
    """Run the ingest pipeline for a single document, yielding progress lines."""
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        run_pipeline(only_doc_id=doc_id)
    for line in buf.getvalue().splitlines():
        yield line


def get_index_health() -> dict:
    """Aggregate stats for the advanced-settings health panel."""
    total_chunks = 0
    counts_by_type: dict[str, int] = {}
    chunks_jsonl = _PARSED_DIR / "chunks.jsonl"
    if chunks_jsonl.exists():
        with chunks_jsonl.open(encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                chunk = json.loads(line)
                etype = chunk.get("element_type", "unknown")
                counts_by_type[etype] = counts_by_type.get(etype, 0) + 1
                total_chunks += 1

    register_count = 0
    general_table_count = 0
    if _DB_PATH.exists():
        con = sqlite3.connect(str(_DB_PATH))
        try:
            register_count = con.execute("SELECT COUNT(*) FROM registers").fetchone()[0]
        except sqlite3.OperationalError:
            pass
        try:
            general_table_count = con.execute("SELECT COUNT(*) FROM general_tables").fetchone()[0]
        except sqlite3.OperationalError:
            pass
        con.close()

    last_indexed = None
    if _CHROMA_DIR.exists():
        mtimes = [p.stat().st_mtime for p in _CHROMA_DIR.rglob("*") if p.is_file()]
        if mtimes:
            last_indexed = max(mtimes)

    return {
        "total_chunks": total_chunks,
        "counts_by_type": counts_by_type,
        "register_count": register_count,
        "general_table_count": general_table_count,
        "last_indexed": last_indexed,
    }


# ── Hardware / acceleration detection ───────────────────────────────────────

def _intel_gpu_available() -> bool:
    try:
        import openvino as ov

        core = ov.Core()
        for device in core.available_devices:
            if not device.startswith("GPU"):
                continue
            name = core.get_property(device, "FULL_DEVICE_NAME")
            if "intel" in str(name).lower():
                return True
        return False
    except Exception:
        return False


def _cuda_available() -> bool:
    try:
        import torch

        return torch.cuda.is_available()
    except Exception:
        return False


def detect_gpu_backend() -> dict:
    """Probe available acceleration backends for the settings UI.

    NVIDIA/CUDA is detected but never offered as usable — R6 only shows it as
    a disabled "coming soon" option, per explicit scope decision.
    """
    intel_gpu = _intel_gpu_available()
    return {
        "intel_gpu": intel_gpu,
        "nvidia_gpu": _cuda_available(),
        "openvino_available": intel_gpu,
        "cuda_available": False,
    }


def save_acceleration_settings(embed_model: str, use_openvino: bool) -> None:
    """Persist EMBED_MODEL / USE_OPENVINO into .env for the next process start."""
    if not _ENV_PATH.exists():
        _ENV_PATH.write_text("", encoding="utf-8")
    set_key(str(_ENV_PATH), "EMBED_MODEL", embed_model)
    set_key(str(_ENV_PATH), "USE_OPENVINO", "1" if use_openvino else "0")
