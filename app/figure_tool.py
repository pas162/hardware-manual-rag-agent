"""
get_figure(figure_id, chip_part) — live SVG extraction from the Smart Manual DB.

figure_id follows the synthesis scheme from ingest/parser_smart_manual_figures.py:
"Figure {section_number}.{n}", where section_number is the dotted numeric prefix
parsed from freeWord.title and n is the 1-based ordinal of the figure within that
section's display_data. This function re-derives the same scheme to re-locate the
source freeWord row and extract the matching <figure> block's <svg> live.
There is no figures.jsonl cache — everything is live, no image_path/figure_server.
"""

import re
import sqlite3
from pathlib import Path

from bs4 import BeautifulSoup

from app.smart_manual_locator import locate
from ingest.parser_smart_manual_figures import section_number, _figure_caption

_RE_FIGURE_ID = re.compile(r"^Figure\s+(.+)\.(\d+)$")

# ── SQLite connection cache, keyed by chip_part ───────────────────────────────

_connections: dict[str, sqlite3.Connection] = {}


def _get_connection(chip_part: str) -> sqlite3.Connection:
    con = _connections.get(chip_part)
    if con is None:
        db_path = locate(chip_part)
        con = sqlite3.connect(str(db_path), check_same_thread=False)
        con.row_factory = sqlite3.Row
        _connections[chip_part] = con
    return con


def _make_citation(chip_part: str, section_title: str, figure_id: str) -> str:
    return f"【{chip_part} Smart Manual | {section_title} | {figure_id}】"


def get_figure(figure_id: str, chip_part: str) -> dict | None:
    """Retrieve a figure by its ID (e.g. 'Figure 13.2.1').

    Returns a dict with figure_id, caption, section_title, svg, citation —
    or None if not found.
    """
    match = _RE_FIGURE_ID.match(figure_id.strip())
    if match is None:
        return None
    target_section_number, ordinal = match.group(1), int(match.group(2))

    try:
        con = _get_connection(chip_part)
    except FileNotFoundError:
        return None

    cur = con.execute("SELECT title, display_data FROM freeWord")
    for row in cur.fetchall():
        title = row["title"]
        if section_number(title) != target_section_number:
            continue

        display_data = row["display_data"] or ""
        if "<figure" not in display_data:
            continue

        soup = BeautifulSoup(display_data, "html.parser")
        figures = soup.find_all("figure")
        if ordinal > len(figures):
            continue

        figure = figures[ordinal - 1]
        svg = figure.find("svg")
        if svg is None:
            continue

        caption = _figure_caption(figure)
        return {
            "figure_id": figure_id,
            "caption": caption,
            "section_title": title,
            "svg": str(svg),
            "citation": _make_citation(chip_part, title, figure_id),
        }

    return None


if __name__ == "__main__":
    import sys
    import json

    fid = sys.argv[1] if len(sys.argv) > 1 else "Figure 1.2.1"
    chip = sys.argv[2] if len(sys.argv) > 2 else "RA6M4"

    result = get_figure(fid, chip)
    if result:
        preview = {**result, "svg": result["svg"][:200] + "..."}
        print(json.dumps(preview, indent=2, ensure_ascii=False))
    else:
        print(f"Figure {fid!r} not found for chip {chip}")
