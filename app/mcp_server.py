"""
MCP server for Hardware User Manual RAG.

Exposes five tools:
  search_um(query, chip_part, top_k=6)               — semantic search (prose + registers + figures + tables)
  register_lookup(name, chip_part)                    — deterministic SQLite register lookup
  query_register_field(register_name, bit_or_symbol, chip_part) — single bit-field lookup
  get_figure(figure_id, chip_part)                    — retrieve a figure by ID
  get_table(table_id, chip_part)                       — deterministic SQLite general-table lookup

Run (SSE transport, keep running in the background):
  python -m app.mcp_server

Server listens on http://localhost:8765/sse by default.
Override with HOST / PORT / MCP_PATH environment variables.

Inspect:
  npx @modelcontextprotocol/inspector
"""

import os

from dotenv import load_dotenv
load_dotenv()

from fastmcp import FastMCP

from app.retriever import search as _search, _get_vectorstore as _warmup_retriever
from app.register_tool import register_lookup as _register_lookup
from app.register_tool import query_register_field as _query_register_field
from app.figure_tool import get_figure as _get_figure
from app.table_tool import get_table as _get_table

mcp = FastMCP("hardware-um")

# --- Transport configuration (env-overridable) ---
HOST = os.getenv("HOST", "localhost")
PORT = int(os.getenv("PORT", "8765"))
MCP_PATH = os.getenv("MCP_PATH", "/sse")

# Eager-load the embedding model and vectorstore at startup so the first
# tool call doesn't time out waiting for sentence-transformers to load.
_warmup_retriever()


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
def query_register_field(register_name: str, bit_or_symbol: str, chip_part: str) -> dict | None:
    """Look up a single bit field within one register, without returning the whole register.

    Matches bit_or_symbol against the field's symbol name (e.g. "IR") first, then
    against its bit index or range (e.g. "5"). Returns null when the register,
    chip_part, or field is unknown.

    Args:
        register_name: Register name, e.g. "IELSRn"
        bit_or_symbol: Bit index (e.g. "5") or symbol name (e.g. "IR")
        chip_part:     Chip identifier, e.g. "RA6M4"
    """
    return _query_register_field(register_name, bit_or_symbol, chip_part)


@mcp.tool()
def get_figure(figure_id: str, chip_part: str) -> dict | None:
    """Retrieve a figure by its ID (e.g. 'Figure 13.2').

    Returns caption, VLM summary, and image path.
    Returns null for unknown figure IDs.

    Args:
        figure_id: Figure identifier, e.g. "Figure 13.2"
        chip_part: Chip identifier, e.g. "RA6M4"
    """
    return _get_figure(figure_id, chip_part)


@mcp.tool()
def get_table(table_id: str, chip_part: str) -> dict | None:
    """Retrieve a general (non-register) table by its ID (e.g. 'table-4.1').

    Returns the full, faithful markdown table, section_path, page, and citation.
    Returns null for unknown table IDs.

    Args:
        table_id:  Table identifier, e.g. "table-4.1" (from a search_um hit's table_id field)
        chip_part: Chip identifier, e.g. "RA6M4"
    """
    return _get_table(table_id, chip_part)


if __name__ == "__main__":
    # SSE transport: one shared server instance for all clients.
    mcp.run(transport="sse", host=HOST, port=PORT, path=MCP_PATH)
