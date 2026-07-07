"""
Build the figure discovery index from <figure> blocks in freeWord.display_data.

Raw captions ("Figure 1.", "Figure 2.", ...) are NOT globally unique — they
reset per section (993 figures total across freeWord, "Figure 1." alone
appears 544 times). figure_id is instead synthesized as
"Figure {section_number}.{n}", where section_number is the dotted numeric
prefix parsed from freeWord.title (e.g. "13.1", "1.2") and n is the 1-based
ordinal of the figure within that section's display_data.

This is a discovery index only — no SVG extraction here. app/figure_tool.py
re-derives the same figure_id scheme when it re-queries the source row live
to extract the <svg>.

Output:
  data/parsed/figures_sm.jsonl
    {"figure_id": str, "caption": str, "section_title": str}
"""

import json
import re
import sqlite3
from pathlib import Path

from bs4 import BeautifulSoup

from app.smart_manual_locator import locate

_RE_SECTION_NUMBER = re.compile(r"^(\d+(?:\.\d+)*)\.?\s")


def section_number(title: str) -> str:
    """Extract the dotted numeric section prefix from a freeWord title.

    e.g. "13.2.1. ICUSARA : ..." -> "13.2.1"; "1. Overview" -> "1".
    Returns "" if the title has no leading numeric prefix.
    """
    match = _RE_SECTION_NUMBER.match(title)
    return match.group(1) if match else ""


def _figure_caption(figure) -> str:
    """Direct text of <figcaption>, excluding nested <span class="figdesc"> notes."""
    figcaption = figure.find("figcaption")
    if figcaption is None:
        return ""
    parts = [
        c if isinstance(c, str) else ""
        for c in figcaption.contents
        if not (hasattr(c, "name") and c.name == "span")
    ]
    return "".join(parts).strip()


def parse_figures(db_path: Path, figures_out: Path) -> int:
    """Parse every freeWord row's <figure> blocks. Returns the figure count."""
    con = sqlite3.connect(str(db_path))
    con.row_factory = sqlite3.Row
    cur = con.execute("SELECT title, display_data FROM freeWord")

    figures_out.parent.mkdir(parents=True, exist_ok=True)

    figure_count = 0
    with figures_out.open("w", encoding="utf-8") as out_f:
        for row in cur.fetchall():
            title = row["title"]
            display_data = row["display_data"] or ""
            if "<figure" not in display_data:
                continue

            soup = BeautifulSoup(display_data, "html.parser")
            figures = soup.find_all("figure")
            if not figures:
                continue

            sec_num = section_number(title)
            for n, figure in enumerate(figures, start=1):
                figure_id = f"Figure {sec_num}.{n}" if sec_num else f"Figure {n}"
                caption = _figure_caption(figure)
                out_f.write(json.dumps({
                    "figure_id": figure_id,
                    "caption": caption,
                    "section_title": title,
                }, ensure_ascii=False) + "\n")
                figure_count += 1

    con.close()
    return figure_count


if __name__ == "__main__":
    import sys

    chip = sys.argv[1] if len(sys.argv) > 1 else "RA6M4"
    db_path = locate(chip)
    figures_out = Path("data/parsed/figures_sm.jsonl")

    figure_count = parse_figures(db_path, figures_out)
    print(f"figure rows: {figure_count}")
