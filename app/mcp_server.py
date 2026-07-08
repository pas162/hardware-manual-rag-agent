"""
MCP server for Hardware User Manual RAG.

Exposes three tools:
  search_um(query, chip_part, top_k=6)   — semantic search (prose + registers + figures)
  register_lookup(name, chip_part)        — deterministic SQLite register lookup
  get_figure(figure_id, chip_part)        — retrieve a figure by ID, and automatically
                                             push it into the Renesas Smart Manual VS Code
                                             extension for display (see _open_vscode_show_figure).
                                             Set SMART_MANUAL_AUTO_SHOW=0 to disable.

Run (stdio — default, used by Cursor/Claude Desktop/RICA via mcp.json):
  python -m app.mcp_server

Run (SSE — persistent HTTP server, survives IDE restarts):
  python -m app.mcp_server --sse
  python -m app.mcp_server --sse --host 127.0.0.1 --port 8765

Inspect:
  npx @modelcontextprotocol/inspector python -m app.mcp_server
"""

# ── CRITICAL: Set offline env BEFORE any imports ─────────────────────────────
# Hugging Face model download happens at import-time of HuggingFaceEmbeddings.
# If HF_HUB_OFFLINE is not set before that, it will hang trying to reach HF servers.
# This is a belt-and-suspenders guard in case the launcher didn't pass env vars.
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("PYTHONUTF8", "1")

import argparse
import json
import platform
import subprocess
import sys
import tempfile
import threading
from pathlib import Path
from urllib.parse import quote

from dotenv import load_dotenv
load_dotenv()

from fastmcp import FastMCP
def _log(msg: str) -> None:
    """Log to stderr only — stdout is reserved for the MCP JSON-RPC stream."""
    print(f"[hardware-um] {msg}", file=sys.stderr, flush=True)


mcp = FastMCP("hardware-um")

# ── Auto-display in the Renesas Smart Manual VS Code extension ───────────────
# get_figure pushes its result straight into VS Code by writing a handoff
# file that the extension watches (and also best-effort opens a `vscode://`
# URI, which helps in some single-VS-Code-instance setups but isn't relied
# on), instead of relying on the calling agent to correctly forward the tool
# result to a second command.
#
# This is intentionally fire-and-forget: we do NOT wait for/confirm that the
# extension actually rendered it (that added latency and, in one setup,
# stalled the whole tool call). `shown_in_vscode` below only reflects
# whether the push itself was attempted successfully, not a confirmed
# display — good enough for now; can be revisited later if a real ack loop
# is needed again.

_VSCODE_SHOW_FIGURE_URI = (
    "vscode://RenesasElectronicsCorporation.renesas-smart-manual/showFigure"
)
_FIGURE_HANDOFF_FILE = Path(tempfile.gettempdir()) / "smart_manual_figure.json"


def _auto_show_enabled() -> bool:
    return os.environ.get("SMART_MANUAL_AUTO_SHOW", "1").strip().lower() not in (
        "0",
        "false",
        "no",
    )


def _open_vscode_show_figure(payload: dict) -> bool:
    """Write the figure payload to a handoff file that the extension's file
    watcher picks up, and best-effort also open VS Code's showFigure URI.

    Fire-and-forget: never waits for the extension to confirm anything, so
    this never adds latency to the tool call. Never raises — any failure
    here is swallowed and just means the figure silently doesn't appear,
    which is NOT an error.
    """
    try:
        _FIGURE_HANDOFF_FILE.write_text(json.dumps(payload), encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        _log(f"could not write figure handoff file (non-fatal): {exc!r}")
        return False

    try:
        uri = f"{_VSCODE_SHOW_FIGURE_URI}?file={quote(str(_FIGURE_HANDOFF_FILE))}"
        system = platform.system()
        if system == "Windows":
            os.startfile(uri)  # type: ignore[attr-defined]
        elif system == "Darwin":
            subprocess.run(["open", uri], check=False)
        else:
            subprocess.run(["xdg-open", uri], check=False)
    except Exception as exc:  # noqa: BLE001
        _log(f"could not open vscode:// URI (non-fatal): {exc!r}")
    return True

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
_warmup_phase: str = "starting"        # human-readable current phase
_warmup_started: bool = False          # guards against double-start
_warmup_started_at: float | None = None  # perf_counter() when warmup began

# A healthy warmup takes ~20-25 s (model load + Chroma + BM25).  If it takes
# dramatically longer it's almost always a stale/hung process stuck on a
# network call to Hugging Face — so we bound the wait and tell the user to
# restart rather than hanging for minutes.
_WARMUP_HARD_LIMIT = 90.0   # seconds; warmup should never legitimately exceed this
_WAIT_TIMEOUT = 60.0        # seconds a search_um call waits before giving up


def _warmup_worker() -> None:
    """Pre-load the embedding model, Chroma vectorstore, and BM25 index."""
    import time
    global _warmup_error, _warmup_elapsed, _warmup_phase, _warmup_started_at
    t0 = time.perf_counter()
    _warmup_started_at = t0
    try:
        _warmup_phase = "loading embedding model + Chroma"
        _log("warmup: loading embedding model + Chroma ...")
        from app.store import get_vectorstore
        get_vectorstore()
        _log(f"warmup: vectorstore ready ({time.perf_counter() - t0:.1f}s)")

        _warmup_phase = "building BM25 index"
        _log("warmup: building BM25 index ...")
        from app.retriever import _get_bm25
        _get_bm25("RA6M4")
        _warmup_elapsed = time.perf_counter() - t0
        _warmup_phase = "ready"
        _log(f"warmup: BM25 ready ({_warmup_elapsed:.1f}s) — all tools ready")
    except Exception as exc:  # noqa: BLE001
        _warmup_error = exc
        _warmup_elapsed = time.perf_counter() - t0
        _warmup_phase = "failed"
        _log(f"warmup FAILED after {_warmup_elapsed:.1f}s: {exc!r}")
    finally:
        _vs_ready.set()


def _start_warmup() -> None:
    """Kick off the background warmup exactly once."""
    global _warmup_started
    if _warmup_started:
        return
    _warmup_started = True
    threading.Thread(target=_warmup_worker, daemon=True, name="warmup").start()


def _ensure_vs_ready(timeout: float = _WAIT_TIMEOUT) -> None:
    """Block until the vectorstore + BM25 are loaded (search_um only)."""
    # If warmup somehow never started (e.g. tool called before __main__ ran),
    # start it now so we don't wait on an event that will never be set.
    _start_warmup()

    if not _vs_ready.wait(timeout=timeout):
        raise TimeoutError(
            f"Backend warmup did not finish within {timeout:.0f}s while '{_warmup_phase}'. "
            "A healthy warmup takes ~20-25 s, so this usually means the server "
            "process is stuck (often on a network call to Hugging Face). "
            "Please restart / reconnect the hardware-um MCP server."
        )
    if _warmup_error is not None:
        raise RuntimeError(
            f"Backend failed to initialise during '{_warmup_phase}': {_warmup_error!r}. "
            "Restart the hardware-um MCP server."
        )


@mcp.tool()
def server_status() -> dict:
    """Return the current warmup state of the hardware-um MCP server.

    Call this first to check whether search_um is ready before issuing a query.
    register_lookup and get_figure are always available immediately.

    Returns a dict with:
      ready         — true when search_um is usable; false while still loading
      status        — "ready" | "warming_up" | "stuck" | "failed"
      message       — human-readable explanation
      phase         — what warmup is currently doing
      warmup_sec    — seconds warmup took (null while still in progress)
      tools_always_available — tools that work regardless of warmup state
    """
    import time
    global _warmup_started_at
    if _warmup_error is not None:
        return {
            "ready": False,
            "status": "failed",
            "message": f"Warmup failed during '{_warmup_phase}': {_warmup_error!r}. Restart the server.",
            "phase": _warmup_phase,
            "warmup_sec": round(_warmup_elapsed, 1) if _warmup_elapsed is not None else None,
            "tools_always_available": ["register_lookup", "get_figure"],
        }
    if _vs_ready.is_set():
        return {
            "ready": True,
            "status": "ready",
            "message": "All tools are ready.",
            "phase": "ready",
            "warmup_sec": round(_warmup_elapsed, 1) if _warmup_elapsed is not None else None,
            "tools_always_available": ["register_lookup", "get_figure"],
        }

    # Still warming up — detect a likely-stuck process (exceeded the hard limit).
    elapsed = (time.perf_counter() - _warmup_started_at) if _warmup_started_at else 0.0
    if elapsed > _WARMUP_HARD_LIMIT:
        return {
            "ready": False,
            "status": "stuck",
            "message": (
                f"Warmup has been running {elapsed:.0f}s (>{_WARMUP_HARD_LIMIT:.0f}s) "
                f"while '{_warmup_phase}'. A healthy warmup takes ~20-25 s, so this "
                "process is almost certainly stuck (often a network call to Hugging "
                "Face). Please restart / reconnect the hardware-um MCP server."
            ),
            "phase": _warmup_phase,
            "warmup_sec": None,
            "tools_always_available": ["register_lookup", "get_figure"],
        }
    return {
        "ready": False,
        "status": "warming_up",
        "message": (
            f"Currently '{_warmup_phase}' ({elapsed:.0f}s elapsed; usually ~20-25 s total). "
            "register_lookup and get_figure are usable now. "
            "Retry server_status in a few seconds before calling search_um."
        ),
        "phase": _warmup_phase,
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
    """Look up a register or bit by name. Returns address, reset value, and all bit fields.

    Search order:
      1. registerList — exact + prefix match on register_symbol_name (e.g. "CACR2", "IELSRn")
      2. bitList      — exact + prefix match on bit_symbol_name (e.g. "CACREFE", "RSCS"),
                        only when step 1 finds nothing.
    Returns a list because a name can appear in multiple peripherals.
    Returns an empty list for unknown names.

    When a bit is matched (result_type = "bit"), each result includes:
      - matched_bit: the specific bit that was found (symbol, bit position, description)
      - bit_fields:  the full bit-table of the parent register for context
      - matched: true on the specific bit entry inside bit_fields

    Args:
        name:      Register or bit name, e.g. "CACR2", "CACREFE", "SCKDIVCR",
                   "IELSRn" (fuzzy-matched as a fallback for PDF-era names like "SCKCR")
        chip_part: Chip identifier, e.g. "RA6M4"
    """
    from app.register_tool import register_lookup as _register_lookup
    return _register_lookup(name, chip_part)


@mcp.tool()
def get_figure(figure_id: str, chip_part: str) -> dict | None:
    """Retrieve a figure by its ID (e.g. 'Figure 13.2.1').

    Returns caption, section_title, citation, and svg (raw SVG markup read
    live from the Smart Manual DB). Returns null for unknown figure IDs.

    As a side effect, this also tries to auto-display the figure in the
    "Renesas Smart Manual" VS Code extension, if it happens to be installed
    and running. This is fire-and-forget, best-effort, and silent: it never
    waits for or confirms that the extension actually rendered anything, so
    it never adds latency or can get "stuck". It is NOT part of what makes
    this tool call successful or not. Whether or not the figure ends up
    visually displayed:
      - Treat this call as fully successful as long as the fields above
        (svg, caption, etc.) were returned.
      - Do NOT retry, do NOT call any other command, and do NOT mention any
        "failure" to the user — `shown_in_vscode` only reflects whether the
        push attempt itself ran, not a confirmed display, so a false value
        is completely normal and not an error.
      - You may still describe the figure to the user in your own words
        using caption/section_title/citation as you normally would.

    Args:
        figure_id: Figure identifier, e.g. "Figure 13.2.1"
        chip_part: Chip identifier, e.g. "RA6M4"
    """
    from app.figure_tool import get_figure as _get_figure
    result = _get_figure(figure_id, chip_part)
    if result is None:
        return None

    shown = _auto_show_enabled() and _open_vscode_show_figure(result)
    result["shown_in_vscode"] = shown
    result["note"] = (
        "This figure was also pushed to the 'Smart Manual' panel in VS Code "
        "(fire-and-forget, unconfirmed) — mention that to the user, no "
        "further action needed."
        if shown
        else (
            "Figure data retrieved successfully (this is not an error). "
            "Auto-display wasn't attempted/available this time, which is "
            "fine and expected. No further action needed; do not retry or "
            "call any other command for this."
        )
    )
    return result


if __name__ == "__main__":
    # Start the heavy warmup in the background so the MCP handshake responds
    # immediately regardless of transport.
    _start_warmup()

    parser = argparse.ArgumentParser(description="Hardware UM MCP Server")
    parser.add_argument(
        "--sse",
        action="store_true",
        help="Run as persistent SSE/HTTP server instead of stdio (survives IDE restarts)",
    )
    parser.add_argument("--host", default="127.0.0.1", help="SSE host (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8765, help="SSE port (default: 8765)")
    args = parser.parse_args()

    if args.sse:
        _log(f"Starting Hardware UM MCP server (SSE) on http://{args.host}:{args.port}/sse")
        _log("Add to mcp.json:  { \"url\": \"http://" + args.host + ":" + str(args.port) + "/sse\" }")
        mcp.run(transport="sse", host=args.host, port=args.port, show_banner=False)
    else:
        _log("Starting Hardware UM MCP server (stdio)")
        mcp.run(show_banner=False)  # stdio — default for Cursor / Claude Desktop / RICA
