"""
MCP server for Hardware User Manual RAG.

Exposes three tools:
  search_um(query, chip_part, top_k=6)   — semantic search (prose + registers + figures)
  register_lookup(name, chip_part)        — deterministic SQLite register lookup
  get_figure(figure_id, chip_part)        — retrieve a figure by ID

Run (stdio — default, used by Cursor/Claude Desktop/RICA via mcp.json):
  python -m app.mcp_server

Run (SSE — persistent HTTP server, survives IDE restarts):
  python -m app.mcp_server --sse
  python -m app.mcp_server --sse --host 127.0.0.1 --port 8765

Inspect:
  npx @modelcontextprotocol/inspector python -m app.mcp_server
"""

import argparse
import os
import sys
import threading

from dotenv import load_dotenv
load_dotenv()

from fastmcp import FastMCP


def _log(msg: str) -> None:
    """Log to stderr only — stdout is reserved for the MCP JSON-RPC stream."""
    print(f"[hardware-um] {msg}", file=sys.stderr, flush=True)


mcp = FastMCP("hardware-um")

# ── Lazy, non-blocking warmup ─────────────────────────────────────────────────
# IMPORTANT: do NOT load the embedding model or start the figure server at import
# time.  Doing so blocks the MCP `initialize` handshake for 10-30 s and makes
# RICA / Cursor report the server as "failed to connect" (handshake timeout).
#
# Instead we kick off warmup in a background thread so `mcp.run()` can answer the
# handshake immediately, and each tool waits on the warmup only when first called.

_vs_ready = threading.Event()   # set when vectorstore + BM25 are loaded
_warmup_error: Exception | None = None
_warmup_elapsed: float | None = None   # seconds taken, set on completion


def _warmup_worker() -> None:
    """Pre-load the embedding model, Chroma vectorstore, and BM25 index."""
    import time
    global _warmup_error, _warmup_elapsed
    t0 = time.perf_counter()
    try:
        _log("warmup: loading embedding model + Chroma ...")
        from app.store import get_vectorstore
        get_vectorstore()
        _log(f"warmup: vectorstore ready ({time.perf_counter() - t0:.1f}s)")

        _log("warmup: building BM25 index ...")
        from app.retriever import _get_bm25
        _get_bm25("RA6M4")
        _warmup_elapsed = time.perf_counter() - t0
        _log(f"warmup: BM25 ready ({_warmup_elapsed:.1f}s) — all tools ready")
    except Exception as exc:  # noqa: BLE001
        _warmup_error = exc
        _warmup_elapsed = time.perf_counter() - t0
        _log(f"warmup FAILED after {_warmup_elapsed:.1f}s: {exc!r}")
    finally:
        _vs_ready.set()


def _ensure_vs_ready(timeout: float = 180.0) -> None:
    """Block until the vectorstore + BM25 are loaded (search_um only)."""
    if not _vs_ready.wait(timeout=timeout):
        raise TimeoutError(
            "Backend warmup did not finish in time — embedding model is still loading. "
            "Please retry in a few seconds."
        )
    if _warmup_error is not None:
        raise RuntimeError(f"Backend failed to initialise: {_warmup_error!r}")


@mcp.tool()
def server_status() -> dict:
    """Return the current warmup state of the hardware-um MCP server.

    Call this first to check whether search_um is ready before issuing a query.
    register_lookup and get_figure are always available immediately.

    Returns a dict with:
      ready         — true when search_um is usable; false while still loading
      status        — "ready" | "warming_up" | "failed"
      message       — human-readable explanation
      warmup_sec    — seconds warmup took (null while still in progress)
      tools_always_available — tools that work regardless of warmup state
    """
    import time
    if _warmup_error is not None:
        return {
            "ready": False,
            "status": "failed",
            "message": f"Warmup failed: {_warmup_error!r}. Restart the server.",
            "warmup_sec": round(_warmup_elapsed, 1) if _warmup_elapsed is not None else None,
            "tools_always_available": ["register_lookup", "get_figure"],
        }
    if _vs_ready.is_set():
        return {
            "ready": True,
            "status": "ready",
            "message": "All tools are ready.",
            "warmup_sec": round(_warmup_elapsed, 1) if _warmup_elapsed is not None else None,
            "tools_always_available": ["register_lookup", "get_figure"],
        }
    return {
        "ready": False,
        "status": "warming_up",
        "message": (
            "Embedding model and BM25 index are still loading. "
            "register_lookup and get_figure are usable now. "
            "Retry server_status in a few seconds before calling search_um."
        ),
        "warmup_sec": None,
        "tools_always_available": ["register_lookup", "get_figure"],
    }


@mcp.tool()
def search_um(query: str, chip_part: str, top_k: int = 6) -> list[dict] | dict:
    """Search the Hardware User Manual for prose, register, or figure content.

    Returns a list of matching chunks, each with section_title, render_text,
    and a citation field.  Returns a refusal dict when no relevant content is found.

    Args:
        query:     Natural-language question or keyword
        chip_part: Chip identifier, e.g. "RA6M4"
        top_k:     Number of results to return (default 6, max 10)
    """
    _ensure_vs_ready()
    from app.retriever import search as _search
    result = _search(query, chip_part, top_k)
    if isinstance(result, str):
        return {"refusal": result}
    return result


@mcp.tool()
def register_lookup(name: str, chip_part: str) -> list[dict]:
    """Look up a register by name. Returns address, reset value, and all bit fields.

    Returns a list because a register name can appear in multiple peripherals.
    Returns an empty list for unknown names.

    Args:
        name:      Register name, e.g. "SCKDIVCR", "IELSRn" (fuzzy-matched against
                   PDF-era names like "SCKCR" as a convenience fallback)
        chip_part: Chip identifier, e.g. "RA6M4"
    """
    from app.register_tool import register_lookup as _register_lookup
    return _register_lookup(name, chip_part)


@mcp.tool()
def get_figure(figure_id: str, chip_part: str) -> dict | None:
    """Retrieve a figure by its ID (e.g. 'Figure 13.2.1').

    Returns caption, section_title, citation, and svg (raw SVG markup read live
    from the Smart Manual DB) so the agent can render the figure directly.
    Returns null for unknown figure IDs.

    Args:
        figure_id: Figure identifier, e.g. "Figure 13.2.1"
        chip_part: Chip identifier, e.g. "RA6M4"
    """
    from app.figure_tool import get_figure as _get_figure
    return _get_figure(figure_id, chip_part)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Hardware UM MCP Server")
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Run as persistent SSE/HTTP server instead of stdio (survives IDE restarts)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="SSE host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="SSE port (default: 8765)")
    args = parser.parse_args()

    # Start the heavy warmup in the background so the MCP handshake responds
    # immediately regardless of transport.
    threading.Thread(target=_warmup_worker, daemon=True).start()

    if args.sse:
        _log(f"Starting Hardware UM MCP server (SSE) on http://{args.host}:{args.port}/sse")
        _log("Add to mcp.json:  { \"url\": \"http://" + args.host + ":" + str(args.port) + "/sse\" }")
        mcp.run(transport="sse", host=args.host, port=args.port, show_banner=False)
    else:
        _log("Starting Hardware UM MCP server (stdio)")
        mcp.run(show_banner=False)  # stdio — default for Cursor / Claude Desktop / RICA
