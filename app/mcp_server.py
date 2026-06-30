"""
MCP server for Hardware User Manual RAG.

Exposes three tools:
  search_um(query, chip_part, top_k=6)   — semantic search (prose + registers + figures)
  register_lookup(name, chip_part)        — deterministic SQLite register lookup
  get_figure(figure_id, chip_part)        — retrieve a figure by ID

Run (stdio — default, used by Cursor/Claude Desktop via mcp.json):
  python -m app.mcp_server

Run (SSE — persistent HTTP server, survives IDE restarts):
  python -m app.mcp_server --sse
  python -m app.mcp_server --sse --host 127.0.0.1 --port 8765

Inspect:
  npx @modelcontextprotocol/inspector python -m app.mcp_server
"""

import argparse
import sys

from dotenv import load_dotenv
load_dotenv()

from fastmcp import FastMCP

from app.store import get_vectorstore as _warmup          # Fix 1+3: single warmup point
from app.retriever import search as _search
from app.register_tool import register_lookup as _register_lookup
from app.figure_tool import get_figure as _get_figure
from app.figure_server import start_figure_server, figure_url as _figure_url

mcp = FastMCP("hardware-um")

# Start HTTP server for figure images (runs in daemon thread, port 7477)
_FIGURE_SERVER_PORT = start_figure_server()

# Eager-load the shared embedding model + vectorstore at startup so the first
# tool call doesn't time out.  Both retriever and figure_tool share this instance.
_warmup()


@mcp.tool()
def search_um(query: str, chip_part: str, top_k: int = 6) -> list[dict] | dict:
    """Search the Hardware User Manual for prose, register, or figure content.

    Returns a list of matching chunks, each with section_path, page, render_text,
    and a citation field.  Returns a refusal dict when no relevant content is found.

    Args:
        query:     Natural-language question or keyword
        chip_part: Chip identifier, e.g. "RA6M4"
        top_k:     Number of results to return (default 6, max 10)
    """
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
        name:      Register name, e.g. "SCKCR", "IELSRn"
        chip_part: Chip identifier, e.g. "RA6M4"
    """
    return _register_lookup(name, chip_part)


@mcp.tool()
def get_figure(figure_id: str, chip_part: str) -> dict | None:
    """Retrieve a figure by its ID (e.g. 'Figure 13.2').

    Returns caption, VLM summary, image_path, and image_data (base64-encoded
    PNG as a data URI: 'data:image/png;base64,...') so the agent can render
    and analyse the figure image directly via vision.
    Returns null for unknown figure IDs.

    Args:
        figure_id: Figure identifier, e.g. "Figure 13.2"
        chip_part: Chip identifier, e.g. "RA6M4"
    """
    import base64
    from pathlib import Path as _Path

    result = _get_figure(figure_id, chip_part)
    if result is None:
        return None

    result["image_url"] = _figure_url(result.get("image_path", ""))

    # Embed image as base64 data URI so the agent can view it via vision
    image_path = result.get("image_path", "")
    if image_path:
        _ROOT = _Path(__file__).resolve().parent.parent
        abs_path = _ROOT / image_path
        if abs_path.is_file():
            raw = abs_path.read_bytes()
            b64 = base64.b64encode(raw).decode("ascii")
            result["image_data"] = f"data:image/png;base64,{b64}"

    return result


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

    if args.sse:
        print(f"Starting Hardware UM MCP server (SSE) on http://{args.host}:{args.port}/sse")
        print("Add to mcp.json:  { \"url\": \"http://" + args.host + ":" + str(args.port) + "/sse\" }")
        mcp.run(transport="sse", host=args.host, port=args.port)
    else:
        mcp.run()  # stdio — default for Cursor / Claude Desktop