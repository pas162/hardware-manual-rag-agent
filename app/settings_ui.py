"""
Gradio Settings UI — add/remove documents and manage index settings without
touching data/registry.json or .env by hand.

Run: python -m app.settings_ui   (serves on http://127.0.0.1:7860)
"""

import re
from pathlib import Path

import gradio as gr

from app.registry_manager import (
    add_document,
    detect_gpu_backend,
    get_index_health,
    list_documents,
    remove_document,
    reset_all_data,
    save_acceleration_settings,
    trigger_ingest,
)
from settings import EMBED_MODEL, USE_OPENVINO

_ROOT = Path(__file__).resolve().parent.parent


# ── Main screen ──────────────────────────────────────────────────────────────

def _status_badge(doc: dict) -> str:
    if not doc["pdf_exists"]:
        return "❌ PDF missing"
    if doc["chunk_count"] > 0:
        return "✅ Indexed"
    return "⚠️ Not yet indexed"


def _documents_markdown() -> str:
    docs = list_documents()
    if not docs:
        return "_No documents yet. Add one below to get started._"

    lines = ["| Document | Chip | Folder | Status | Chunks | Registers |",
             "|---|---|---|---|---|---|"]
    for doc in docs:
        lines.append(
            f"| {doc['doc_id']} | {doc['chip_part']} | {doc['folder'] or '(root)'} "
            f"| {_status_badge(doc)} | {doc['chunk_count']} | {doc['register_count']} |"
        )
    return "\n".join(lines)


def _doc_id_choices() -> list[str]:
    return [d["doc_id"] for d in list_documents()]


def _suggest_ids(pdf_file) -> tuple[str, str]:
    if not pdf_file:
        return "", "1.00"
    stem = Path(pdf_file).stem
    doc_id = re.sub(r"[^A-Za-z0-9_-]+", "_", stem).upper()
    return doc_id, "1.00"


def _add_and_ingest(pdf_file, chip_part, folder, doc_id, revision, progress=gr.Progress()):
    if not pdf_file:
        return "⚠️ Please choose a PDF file first.", _documents_markdown(), gr.update(choices=_doc_id_choices())
    if not chip_part.strip():
        return "⚠️ Please enter a chip part name.", _documents_markdown(), gr.update(choices=_doc_id_choices())
    if not doc_id.strip():
        return "⚠️ Please enter a document ID.", _documents_markdown(), gr.update(choices=_doc_id_choices())

    folder = folder.strip() or "default"

    try:
        entry = add_document(doc_id.strip(), revision.strip() or "1.00", chip_part.strip(), folder, pdf_file)
    except ValueError as e:
        return f"❌ {e}", _documents_markdown(), gr.update(choices=_doc_id_choices())

    log_lines = [f"Added {entry['doc_id']} -> {entry['path']}", "Starting ingest..."]
    for line in trigger_ingest(entry["doc_id"]):
        log_lines.append(line)

    log_lines.append("✅ Ready to use.")
    return "\n".join(log_lines), _documents_markdown(), gr.update(choices=_doc_id_choices())


def _remove_selected(doc_id, wipe_data):
    if not doc_id:
        return "⚠️ Select a document to remove.", _documents_markdown(), gr.update(choices=_doc_id_choices())
    remove_document(doc_id, wipe_data=wipe_data)
    return f"Removed {doc_id} (data wiped: {wipe_data}).", _documents_markdown(), gr.update(choices=_doc_id_choices())


# ── Advanced panel ───────────────────────────────────────────────────────────

def _backend_choices() -> tuple[list[str], str]:
    backend = detect_gpu_backend()
    choices = ["CPU (PyTorch)"]
    default = "CPU (PyTorch)"
    if backend["intel_gpu"]:
        choices.append("Intel GPU (OpenVINO)")
        default = "Intel GPU (OpenVINO)"
    choices.append("NVIDIA GPU (CUDA) — coming soon")
    if USE_OPENVINO and backend["intel_gpu"]:
        default = "Intel GPU (OpenVINO)"
    return choices, default


def _save_settings(embed_model, backend_choice):
    use_openvino = backend_choice == "Intel GPU (OpenVINO)"
    save_acceleration_settings(embed_model.strip() or EMBED_MODEL, use_openvino)
    return (
        "Saved. Restart the ingest pipeline / MCP server for changes to take effect.\n"
        "⚠️ Changing the embedding model invalidates the existing index for ALL documents "
        "— run 'Reset all indexed data' and re-ingest."
    )


def _health_markdown() -> str:
    health = get_index_health()
    lines = [
        f"**Total chunks:** {health['total_chunks']}",
        f"**Registers:** {health['register_count']}",
        f"**General tables:** {health['general_table_count']}",
    ]
    if health["counts_by_type"]:
        lines.append("**By type:** " + ", ".join(f"{k}={v}" for k, v in sorted(health["counts_by_type"].items())))
    if health["last_indexed"]:
        import datetime
        lines.append(f"**Last indexed:** {datetime.datetime.fromtimestamp(health['last_indexed'])}")
    else:
        lines.append("**Last indexed:** never")
    return "\n\n".join(lines)


def _reset_all(confirm_text):
    if confirm_text.strip().lower() != "reset":
        return "⚠️ Type 'reset' exactly to confirm.", _health_markdown(), _documents_markdown()
    reset_all_data()
    return "✅ All indexed data wiped. Re-ingest each document to rebuild.", _health_markdown(), _documents_markdown()


# ── Layout ───────────────────────────────────────────────────────────────────

def build_app() -> gr.Blocks:
    backend_choices, backend_default = _backend_choices()

    with gr.Blocks(title="Hardware Manual RAG — Settings") as demo:
        gr.Markdown("# Hardware Manual RAG\nManage the manuals this assistant can answer questions about.")

        docs_view = gr.Markdown(_documents_markdown())

        with gr.Accordion("+ Add a document", open=False):
            pdf_input = gr.File(label="PDF file", file_types=[".pdf"], type="filepath")
            chip_part_input = gr.Textbox(label="Chip / part name", placeholder="e.g. RA6M4")
            folder_input = gr.Textbox(label="Folder", placeholder="e.g. ra6m4 (created if new)", value="default")
            doc_id_input = gr.Textbox(label="Document ID (auto-suggested, editable)")
            revision_input = gr.Textbox(label="Revision", value="1.00")
            add_button = gr.Button("Add + Ingest", variant="primary")
            add_log = gr.Textbox(label="Progress", lines=10, interactive=False)

            pdf_input.change(_suggest_ids, inputs=pdf_input, outputs=[doc_id_input, revision_input])

        with gr.Accordion("Remove a document", open=False):
            remove_dropdown = gr.Dropdown(label="Document", choices=_doc_id_choices())
            wipe_checkbox = gr.Checkbox(label="Also delete its indexed data", value=True)
            remove_button = gr.Button("Remove", variant="stop")
            remove_log = gr.Textbox(label="Result", interactive=False)

        add_button.click(
            _add_and_ingest,
            inputs=[pdf_input, chip_part_input, folder_input, doc_id_input, revision_input],
            outputs=[add_log, docs_view, remove_dropdown],
        )
        remove_button.click(
            _remove_selected,
            inputs=[remove_dropdown, wipe_checkbox],
            outputs=[remove_log, docs_view, remove_dropdown],
        )

        with gr.Accordion("⚙ Settings (advanced)", open=False):
            gr.Markdown("### Embedding & acceleration")
            embed_model_input = gr.Textbox(label="Embedding model (HuggingFace repo id)", value=EMBED_MODEL)
            backend_radio = gr.Radio(
                label="Acceleration backend",
                choices=backend_choices,
                value=backend_default,
            )
            gr.Markdown(
                "_NVIDIA GPU (CUDA) support is not implemented yet — shown for visibility only._"
            )
            save_settings_button = gr.Button("Save settings")
            settings_log = gr.Textbox(label="Result", interactive=False)
            save_settings_button.click(
                _save_settings, inputs=[embed_model_input, backend_radio], outputs=settings_log
            )

            gr.Markdown("### Index health")
            health_view = gr.Markdown(_health_markdown())
            refresh_health_button = gr.Button("Refresh")
            refresh_health_button.click(_health_markdown, outputs=health_view)

            gr.Markdown("### Danger zone")
            gr.Markdown("Wipes the entire index (Chroma + SQLite + parsed intermediates) "
                        "for **every** document. PDFs and registry.json are kept.")
            reset_confirm_input = gr.Textbox(label="Type 'reset' to confirm")
            reset_button = gr.Button("Reset all indexed data", variant="stop")
            reset_log = gr.Textbox(label="Result", interactive=False)
            reset_button.click(
                _reset_all, inputs=reset_confirm_input, outputs=[reset_log, health_view, docs_view]
            )

    return demo


if __name__ == "__main__":
    app = build_app()
    app.launch()
