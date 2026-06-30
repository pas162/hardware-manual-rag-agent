"""
Eval runner — calls MCP tools directly (no agent in loop).

Input:  eval/golden_set.csv
Output: eval/results.md  + prints pass rate

Usage:
  python -m eval.run
"""

import csv
import sys
from pathlib import Path

from dotenv import load_dotenv
load_dotenv()

# Import tools directly
sys.path.insert(0, str(Path(__file__).parent.parent))
from app.retriever import search as _search
from app.register_tool import register_lookup as _register_lookup
from app.figure_tool import get_figure as _get_figure

CHIP_PART = "RA6M4"


# ── Scoring helpers ────────────────────────────────────────────────────────────

def _page_in_range(page: int, page_range: str) -> bool:
    if not page_range:
        return True
    try:
        lo, hi = page_range.split("-")
        return int(lo) <= page <= int(hi)
    except Exception:
        return False


def _score_search_um(result, row: dict) -> tuple[bool, str]:
    if isinstance(result, str):
        return False, "false_refusal"
    if not isinstance(result, list) or not result:
        return False, "false_refusal"

    expected_section = row.get("expected_section", "")
    expected_page_range = row.get("expected_page_range", "")

    # Check any returned chunk matches section and page
    for chunk in result:
        section_ok = not expected_section or expected_section.lstrip("§") in (chunk.get("section_path") or "")
        page_ok = _page_in_range(chunk.get("page", 0), expected_page_range)
        if section_ok and page_ok:
            if not chunk.get("citation"):
                return False, "missing_citation"
            return True, "pass"

    # Find failure reason
    for chunk in result:
        if not chunk.get("citation"):
            return False, "missing_citation"

    sections = [c.get("section_path", "") for c in result]
    pages = [c.get("page", 0) for c in result]
    if expected_section and not any(expected_section.lstrip("§") in s for s in sections):
        return False, "wrong_section"
    if expected_page_range and not any(_page_in_range(p, expected_page_range) for p in pages):
        return False, "wrong_page"
    return False, "wrong_section"


def _score_register_lookup(result, row: dict) -> tuple[bool, str]:
    expected_register = row.get("expected_register", "")
    expected_section = row.get("expected_section", "")
    expected_page_range = row.get("expected_page_range", "")

    if not isinstance(result, list) or not result:
        return False, "false_refusal"

    for rec in result:
        name_ok = not expected_register or expected_register.rstrip("n").lower() in rec.get("register_name", "").lower()
        section_ok = not expected_section or expected_section.lstrip("§") in (rec.get("section_path") or "")
        page_ok = _page_in_range(rec.get("page", 0), expected_page_range)
        has_citation = bool(rec.get("citation"))
        # Check bit_fields is non-empty
        has_bits = bool(rec.get("bit_fields"))

        if not has_citation:
            return False, "missing_citation"
        if not has_bits:
            return False, "hallucinated_register"
        if name_ok and section_ok and page_ok:
            return True, "pass"

    return False, "wrong_section"


def _score_get_figure(result, row: dict) -> tuple[bool, str]:
    expected_figure_id = row.get("expected_figure_id", "")
    expected_section = row.get("expected_section", "")
    expected_page_range = row.get("expected_page_range", "")

    if result is None:
        return False, "false_refusal"

    fid_ok = not expected_figure_id or expected_figure_id.lower() in result.get("figure_id", "").lower()
    section_ok = not expected_section or expected_section.lstrip("§") in (result.get("section_path") or "")
    page_ok = _page_in_range(result.get("page", 0), expected_page_range)
    has_citation = bool(result.get("citation"))

    if not has_citation:
        return False, "missing_citation"
    if fid_ok and section_ok and page_ok:
        return True, "pass"
    if not fid_ok:
        return False, "wrong_section"
    return False, "wrong_page"


# ── Runner ─────────────────────────────────────────────────────────────────────

def run_eval(golden_csv: Path, results_md: Path) -> float:
    rows = []
    with golden_csv.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)

    passes = 0
    failures: list[dict] = []

    for row in rows:
        question = row["question"]
        tool = row.get("tool") or row.get("expected_tool", "")

        try:
            if tool == "search_um":
                result = _search(question, CHIP_PART)
                ok, reason = _score_search_um(result, row)
            elif tool == "register_lookup":
                name = row.get("expected_register", "").rstrip("n") or question.split()[0]
                result = _register_lookup(name, CHIP_PART)
                ok, reason = _score_register_lookup(result, row)
            elif tool == "get_figure":
                fid = row.get("expected_figure_id", "")
                result = _get_figure(fid, CHIP_PART)
                ok, reason = _score_get_figure(result, row)
            else:
                ok, reason = False, "unknown_tool"
        except Exception as e:
            ok, reason = False, f"error: {e}"

        if ok:
            passes += 1
            print(f"  PASS  [{tool}] {question[:70]}")
        else:
            failures.append({"question": question, "tool": tool, "reason": reason})
            print(f"  FAIL  [{tool}] {question[:70]}  ({reason})")

    total = len(rows)
    pass_rate = passes / total if total else 0

    # Write results.md
    _write_results(results_md, rows, failures, passes, total, pass_rate)

    print(f"\nPass rate: {passes}/{total} = {pass_rate:.0%}")
    return pass_rate


def _write_results(
    results_md: Path,
    rows: list[dict],
    failures: list[dict],
    passes: int,
    total: int,
    pass_rate: float,
) -> None:
    from collections import Counter

    failure_counts = Counter(f["reason"] for f in failures)

    lines = [
        "# Eval Results",
        "",
        f"**Pass rate:** {passes}/{total} = {pass_rate:.0%}  (target ≥ 80%)",
        "",
        "## Failure Breakdown",
        "",
        "| Code | Count | Meaning |",
        "|---|---|---|",
        f"| `wrong_section` | {failure_counts.get('wrong_section', 0)} | Returned chunk's section does not match expected |",
        f"| `wrong_page` | {failure_counts.get('wrong_page', 0)} | Page outside expected range |",
        f"| `hallucinated_register` | {failure_counts.get('hallucinated_register', 0)} | Register tool returned record with empty bit_fields |",
        f"| `missing_citation` | {failure_counts.get('missing_citation', 0)} | Returned chunk has no citation field |",
        f"| `false_refusal` | {failure_counts.get('false_refusal', 0)} | Tool returned refusal for a query that had sufficient matching content |",
        "",
        "## Failed Questions",
        "",
        "| Tool | Question | Reason |",
        "|---|---|---|",
    ]
    for f in failures:
        lines.append(f"| `{f['tool']}` | {f['question']} | `{f['reason']}` |")

    results_md.parent.mkdir(parents=True, exist_ok=True)
    results_md.write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--golden", type=Path, default=Path("eval/golden_set_v2.csv"),
                        help="Path to golden set CSV (default: eval/golden_set_v2.csv)")
    parser.add_argument("--results", type=Path, default=Path("eval/results.md"))
    args = parser.parse_args()

    # Fall back to v1 if v2 not yet generated
    golden_csv = args.golden
    if not golden_csv.exists() and golden_csv == Path("eval/golden_set_v2.csv"):
        golden_csv = Path("eval/golden_set.csv")
        print(f"golden_set_v2.csv not found, falling back to {golden_csv}")

    print(f"Running eval against {golden_csv} ...")
    rate = run_eval(golden_csv, args.results)
    print(f"\nResults written to {args.results}")
    sys.exit(0 if rate >= 0.80 else 1)
