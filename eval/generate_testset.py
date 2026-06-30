"""
eval/generate_testset.py
Generate eval/golden_set_v2.csv from actual document chunks.

Three tracks:
  Track 1 — search_um      (60 q): Direct LLM on diverse prose + table chunks
  Track 2 — register_lookup (25 q): Template-based, deterministic, no LLM
  Track 3 — get_figure      (15 q): LLM-generated from figure caption + section

Usage:
  python -m eval.generate_testset                     # full run, 100 questions
  python -m eval.generate_testset --pilot             # 5 questions, sanity check
  python -m eval.generate_testset --size 30           # custom Track 1 size
  python -m eval.generate_testset --track 2           # run only Track 2
  python -m eval.generate_testset --output my.csv     # custom output path
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import os
import random
import re
import sys
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

load_dotenv()

# ── Constants ──────────────────────────────────────────────────────────────────

CHUNKS_PATH = Path("data/parsed/chunks.jsonl")
DEFAULT_OUT = Path("eval/golden_set_v2.csv")
PILOT_OUT   = Path("eval/golden_set_pilot.csv")
CACHE_DB    = Path(".eval_cache.db")

OPENAI_API_BASE = os.getenv("OPENAI_API_BASE", "")
OPENAI_API_KEY  = os.getenv("OPENAI_API_KEY", "")
OPENAI_MODEL    = os.getenv("OPENAI_MODEL", "databricks-claude-sonnet-4-6")
EMBED_MODEL     = os.getenv("EMBED_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

os.environ["HF_HUB_OFFLINE"]       = os.getenv("HF_HUB_OFFLINE", "1")
os.environ["TRANSFORMERS_OFFLINE"] = os.getenv("HF_HUB_OFFLINE", "1")

REGISTER_TEMPLATES = [
    "What is the reset value of {name}?",
    "What does the {field} field in {name} control?",
    "What is the address of {name}?",
    "What bits are reserved in {name}?",
]

CSV_FIELDS = [
    "id", "question", "expected_answer", "expected_tool",
    "expected_section", "expected_page_range",
    "expected_register", "expected_figure_id",
]

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
log = logging.getLogger(__name__)


# ── Setup helpers ──────────────────────────────────────────────────────────────

def _check_endpoint() -> None:
    """Fail fast if the LLM endpoint is unreachable."""
    import urllib.request, urllib.error
    if not OPENAI_API_BASE:
        sys.exit("ERROR: OPENAI_API_BASE not set in .env")
    if not OPENAI_API_KEY:
        sys.exit("ERROR: OPENAI_API_KEY not set in .env")
    host = OPENAI_API_BASE.split("/")[2]  # e.g. "10.210.106.4:8080"
    try:
        # lightweight HEAD — only checks TCP reachability
        req = urllib.request.Request(
            f"{OPENAI_API_BASE.rstrip('/')}/models",
            headers={"Authorization": f"Bearer {OPENAI_API_KEY}"},
        )
        urllib.request.urlopen(req, timeout=5)
    except urllib.error.HTTPError as e:
        if e.code in (401, 403, 404, 405):
            return  # endpoint reachable, auth/path issue is separate
        log.warning("Endpoint check returned HTTP %s — proceeding anyway", e.code)
    except Exception as e:
        sys.exit(f"ERROR: LLM endpoint {OPENAI_API_BASE!r} is unreachable: {e}\n"
                 "Check that the proxy at 10.210.106.4:8080 is up and OPENAI_API_KEY is correct.")


def _build_llm():
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(
        model=OPENAI_MODEL,
        base_url=OPENAI_API_BASE,
        api_key=OPENAI_API_KEY,
        temperature=0.7,
        max_tokens=2000,
        timeout=60,
        max_retries=3,
    )


def _build_embeddings():
    from langchain_huggingface import HuggingFaceEmbeddings
    return HuggingFaceEmbeddings(model_name=EMBED_MODEL)


def _setup_cache() -> None:
    """Configure LangChain SQLite LLM cache to avoid re-spending tokens on reruns."""
    try:
        from langchain_core.globals import set_llm_cache
        from langchain_community.cache import SQLiteCache
        set_llm_cache(SQLiteCache(database_path=str(CACHE_DB)))
        log.info("LLM cache: %s", CACHE_DB)
    except Exception:
        log.warning("LLM cache not available — proceeding without cache")
# ── Chunk loading ──────────────────────────────────────────────────────────────

def _load_chunks() -> dict[str, list[dict]]:
    by_type: dict[str, list[dict]] = {
        "prose": [], "table": [], "register_row": [], "figure": []
    }
    with CHUNKS_PATH.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            chunk = json.loads(line)
            etype = chunk.get("element_type", "prose")
            by_type.setdefault(etype, []).append(chunk)

    for etype, lst in by_type.items():
        log.info("  %-14s %d chunks", etype, len(lst))
    return by_type


# ── Shared helpers ─────────────────────────────────────────────────────────────

def _page_range(chunk: dict) -> str:
    lo = chunk.get("page_start", 0)
    hi = chunk.get("page_end", lo)
    return f"{lo}-{hi}"


def _section_short(section_path: str) -> str:
    m = re.match(r"(§[\d.]+)", section_path)
    return m.group(1) if m else section_path


def _make_row(idx: int, question: str, tool: str, expected_answer: str,
              section: str, page_range: str,
              register: str = "", figure_id: str = "") -> dict:
    return {
        "id": f"q{idx:03d}",
        "question": question,
        "expected_answer": expected_answer,
        "expected_tool": tool,
        "expected_section": section,
        "expected_page_range": page_range,
        "expected_register": register,
        "expected_figure_id": figure_id,
    }


# ── Track 1: search_um via Ragas ───────────────────────────────────────────────

def _merge_chunks_for_ragas(chunks: list[dict], target_chars: int = 2000) -> list[dict]:
    """Merge same-section chunks into longer documents (~500 ragas tokens).

    Ragas needs >100 tokens per doc. Our chunks are ~100 ragas tokens each
    (~500 chars), so we merge ~4 per section to hit the threshold.
    Metadata (section_path, page_start) is taken from the first chunk in each group.
    """
    by_section: dict[str, list[dict]] = {}
    for c in chunks:
        sec = c.get("section_path", "unknown")
        by_section.setdefault(sec, []).append(c)

    merged: list[dict] = []
    for sec, sec_chunks in by_section.items():
        buf_texts: list[str] = []
        buf_page_start = sec_chunks[0].get("page_start", 0)
        buf_page_end   = sec_chunks[0].get("page_end", 0)
        anchor = sec_chunks[0]

        for c in sec_chunks:
            text = c.get("render_text", "").strip()
            if not text:
                continue
            buf_texts.append(text)
            buf_page_end = max(buf_page_end, c.get("page_end", 0))

            if sum(len(t) for t in buf_texts) >= target_chars:
                merged.append({
                    **anchor,
                    "render_text": "\n\n".join(buf_texts),
                    "page_start": buf_page_start,
                    "page_end": buf_page_end,
                })
                buf_texts = []
                buf_page_start = c.get("page_end", buf_page_end)
                buf_page_end   = buf_page_start
                anchor = c

        if buf_texts:
            merged.append({
                **anchor,
                "render_text": "\n\n".join(buf_texts),
                "page_start": buf_page_start,
                "page_end": buf_page_end,
            })

    return merged


def _sample_diverse(merged: list[dict], n: int, max_per_section: int = 5) -> list[dict]:
    """Sample merged docs with a per-section cap to ensure section diversity.

    Default cap of 5 per section means for a 240-doc pool spread across 60+
    sections, each section contributes at most 5 docs → questions span the
    whole manual, not just a few large sections.
    """
    by_section: dict[str, list[dict]] = {}
    for c in merged:
        sec = c.get("section_path", "unknown")
        by_section.setdefault(sec, []).append(c)

    pool: list[dict] = []
    for sec_chunks in by_section.values():
        pool.extend(random.sample(sec_chunks, min(max_per_section, len(sec_chunks))))

    return random.sample(pool, min(n, len(pool)))


def _match_context_to_chunk(
    ctx_text: str,
    text_to_chunk: dict[str, dict],
    embeddings=None,
) -> Optional[dict]:
    """Find source chunk for a Ragas-generated context via exact then fuzzy match."""
    ctx_text = ctx_text.strip()

    # 1. Exact key match
    if ctx_text in text_to_chunk:
        return text_to_chunk[ctx_text]

    # 2. Partial prefix match (Ragas may truncate)
    probe = ctx_text[:120]
    for text, chunk in text_to_chunk.items():
        if probe in text or ctx_text[:80] in text:
            return chunk

    # 3. Embedding similarity fallback
    if embeddings is not None:
        try:
            import numpy as np
            ctx_emb  = embeddings.embed_query(ctx_text)
            best_score, best_chunk = -1.0, None
            for text, chunk in text_to_chunk.items():
                doc_emb = embeddings.embed_query(text[:500])
                sim = float(np.dot(ctx_emb, doc_emb) /
                            (np.linalg.norm(ctx_emb) * np.linalg.norm(doc_emb) + 1e-9))
                if sim > best_score:
                    best_score, best_chunk = sim, chunk
            if best_score > 0.75:
                return best_chunk
        except Exception as e:
            log.debug("Embedding fallback failed: %s", e)

    return None


SEARCH_UM_PROMPT = """\
You are generating evaluation questions for a hardware documentation QA system \
(Renesas RA6M4 Hardware User Manual).

Below is an excerpt from section "{section_path}" (page {page}):

---
{text}
---

Write exactly {n} distinct questions that a hardware engineer would ask, \
where each answer is directly found in this excerpt.

Rules:
- Questions must be specific and technical (register names, bit fields, \
  signal names, operation modes, timing constraints, etc.).
- Vary question types: "what is", "how does", "under what condition", \
  "what is the difference between", etc.
- Output ONLY a numbered list, one question per line, no extra text.
  Example:
  1. What is the reset value of register X?
  2. How does the Y block handle overflow?
"""


def generate_track1(prose_chunks: list[dict], table_chunks: list[dict],
                    size: int, llm, _embeddings) -> list[dict]:
    log.info("\n[Track 1] Generating %d search_um questions via direct LLM...", size)

    all_chunks = prose_chunks + table_chunks
    # Use individual chunks (not merged) — each chunk maps directly to a ground-truth source
    sampled = _sample_diverse(all_chunks, size * 2, max_per_section=5)
    log.info("  Sampled %d individual chunks (target %d questions)", len(sampled), size)

    # Aim for 1-2 questions per chunk; stop once we hit size
    qs_per_chunk = max(1, round(size / len(sampled))) if sampled else 1

    rows: list[dict] = []
    skipped = 0

    for chunk in sampled:
        if len(rows) >= size:
            break
        text = chunk.get("render_text", "").strip()
        if not text or len(text) < 80:
            skipped += 1
            continue

        section = chunk.get("section_path", "")
        page    = chunk.get("page_start", 0)
        n_ask   = min(qs_per_chunk, size - len(rows))

        prompt = SEARCH_UM_PROMPT.format(
            section_path=section,
            page=page,
            text=text[:1200],
            n=n_ask,
        )

        try:
            from langchain_core.messages import HumanMessage
            resp = llm.invoke([HumanMessage(content=prompt)])
            raw = resp.content.strip()
        except Exception as e:
            log.warning("  LLM call failed for §%s p%s: %s", section, page, e)
            skipped += 1
            continue

        # Parse numbered list "1. Question text"
        questions = []
        for line in raw.splitlines():
            line = line.strip()
            m = re.match(r"^\d+[\.\)]\s+(.+)", line)
            if m:
                q = m.group(1).strip()
                if len(q) > 15:
                    questions.append(q)

        if not questions:
            log.warning("  No questions parsed from LLM output for §%s p%s", section, page)
            log.debug("  Raw: %s", raw[:200])
            skipped += 1
            continue

        for q in questions[:n_ask]:
            rows.append(_make_row(
                idx=0,
                question=q,
                tool="search_um",
                expected_answer=text[:200].strip(),
                section=_section_short(section),
                page_range=_page_range(chunk),
            ))
            log.info("  §%s p%s → %s", _section_short(section), page, q[:70])

    log.info("  Track 1: %d questions (%d chunks skipped)", len(rows), skipped)
    return rows[:size]


# ── Track 2: register_lookup via templates ─────────────────────────────────────

def generate_track2(register_chunks: list[dict], n: int) -> list[dict]:
    log.info("\n[Track 2] Generating %d register_lookup questions (templates)...", n)

    # Deduplicate by register_name; keep best chunk per register
    by_register: dict[str, dict] = {}
    fields_by_register: dict[str, list[str]] = {}

    for c in register_chunks:
        name = c.get("register_name", "").strip()
        if not name:
            continue
        if name not in by_register:
            by_register[name] = c
            fields_by_register[name] = []
        # Extract field name from render_text ("FIELD | bits | desc")
        text = c.get("render_text", "")
        m = re.match(r"([A-Z_][A-Z0-9_]{1,})\s*\|", text)
        if m:
            field = m.group(1)
            if field not in fields_by_register[name]:
                fields_by_register[name].append(field)

    all_names = list(by_register.keys())
    random.shuffle(all_names)
    selected = all_names[:n]
    log.info("  %d unique registers found, selecting %d", len(all_names), len(selected))

    rows: list[dict] = []
    for i, name in enumerate(selected):
        chunk = by_register[name]
        fields = fields_by_register.get(name, [])
        field = fields[0] if fields else name

        template = REGISTER_TEMPLATES[i % len(REGISTER_TEMPLATES)]

        # Graceful fallback: skip "field" templates if no fields extracted
        if "{field}" in template and not fields:
            template = REGISTER_TEMPLATES[0]  # reset value — always works

        question = template.format(name=name, field=field)

        # expected_answer: pull from chunk metadata or render_text
        render = chunk.get("render_text", "")
        expected_answer = render[:200].strip() if render else f"See register {name}."

        rows.append(_make_row(
            idx=0,
            question=question,
            tool="register_lookup",
            expected_answer=expected_answer,
            section=_section_short(chunk.get("section_path", "")),
            page_range=_page_range(chunk),
            register=name,
        ))

    log.info("  Track 2: %d questions", len(rows))
    return rows


# ── Track 3: get_figure via LLM ────────────────────────────────────────────────

FIGURE_PROMPT = """\
You are generating an evaluation question for a hardware documentation retrieval system.

Context:
- Section: {section_path}
- Figure ID: {figure_id}
- Figure caption: "{caption}"

Task: Write ONE natural, specific question that a hardware engineer would ask, where \
the answer is contained in or directly relates to this figure.

Rules:
- Use technical terms from the section context.
- Avoid generic phrases like "what does this figure show".
- Output ONLY the question, no preamble, no quotes.
"""


def generate_track3(figure_chunks: list[dict], n: int, llm) -> list[dict]:
    log.info("\n[Track 3] Generating %d get_figure questions via LLM...", n)

    usable = [
        c for c in figure_chunks
        if c.get("figure_id") and c.get("render_text", "").strip()
    ]
    log.info("  %d figure chunks with non-empty caption", len(usable))

    sampled = random.sample(usable, min(n, len(usable)))
    rows: list[dict] = []

    for c in sampled:
        figure_id   = c["figure_id"]
        caption     = c.get("render_text", "")[:300].strip()
        section_path = c.get("section_path", "")

        prompt = FIGURE_PROMPT.format(
            section_path=section_path,
            figure_id=figure_id,
            caption=caption,
        )

        try:
            from langchain_core.messages import HumanMessage
            resp = llm.invoke([HumanMessage(content=prompt)])
            question = resp.content.strip().strip('"').strip("'")
        except Exception as e:
            log.warning("  LLM call failed for %s: %s — using fallback", figure_id, e)
            question = f"Show me the {figure_id} diagram in the {_section_short(section_path)} section."

        expected_answer = f"See {figure_id}: {caption[:150]}"
        log.info("  %s → %s", figure_id, question[:75])

        rows.append(_make_row(
            idx=0,
            question=question,
            tool="get_figure",
            expected_answer=expected_answer,
            section=_section_short(section_path),
            page_range=_page_range(c),
            figure_id=figure_id,
        ))

    log.info("  Track 3: %d questions", len(rows))
    return rows


# ── Writer ─────────────────────────────────────────────────────────────────────

def write_csv(rows: list[dict], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    # Assign sequential IDs
    for i, r in enumerate(rows, start=1):
        r["id"] = f"q{i:03d}"
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    log.info("Wrote %d rows → %s", len(rows), out)


# ── Main ───────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Generate RAG eval test set from chunks")
    parser.add_argument("--pilot", action="store_true",
                        help="Run Track 1 with size=5 only, output to golden_set_pilot.csv")
    parser.add_argument("--size", type=int, default=60,
                        help="Track 1 (search_um) question count (default 60)")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--track", choices=["all", "1", "2", "3"], default="all",
                        help="Run only specific track (default: all)")
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    random.seed(args.seed)

    if args.pilot:
        args.size   = 5
        args.output = PILOT_OUT
        args.track  = "1"
        log.info("=== PILOT MODE: Track 1 only, size=5 → %s ===", PILOT_OUT)

    # ── Connectivity check ──────────────────────────────────────────────────
    log.info("Checking LLM endpoint (%s)...", OPENAI_API_BASE)
    _check_endpoint()
    log.info("  OK")

    # ── Setup ───────────────────────────────────────────────────────────────
    _setup_cache()
    llm        = _build_llm()
    embeddings = _build_embeddings()

    # ── Load chunks ─────────────────────────────────────────────────────────
    log.info("\nLoading chunks from %s...", CHUNKS_PATH)
    by_type = _load_chunks()

    # ── Run tracks ──────────────────────────────────────────────────────────
    all_rows: list[dict] = []

    # get_openai_callback() is a one-shot context manager — create a fresh
    # instance per track instead of reusing the same one across multiple
    # with-blocks (which raises AttributeError on the second entry).
    try:
        from langchain_community.callbacks import get_openai_callback
        _has_cb = True
    except ImportError:
        _has_cb = False

    def run_with_cb(fn, *a, **kw):
        """Run fn(*a, **kw) inside a fresh OpenAI callback if available."""
        if _has_cb:
            from langchain_community.callbacks import get_openai_callback
            with get_openai_callback() as cb:
                result = fn(*a, **kw)
            return result, cb
        return fn(*a, **kw), None

    if args.track in ("all", "1"):
        rows1, cb1 = run_with_cb(
            generate_track1,
            by_type["prose"], by_type["table"],
            args.size, llm, embeddings,
        )
        all_rows += rows1
    else:
        cb1 = None

    if args.track in ("all", "2") and not args.pilot:
        rows2, _ = run_with_cb(generate_track2, by_type["register_row"], 25)
        all_rows += rows2

    if args.track in ("all", "3") and not args.pilot:
        rows3, cb3 = run_with_cb(generate_track3, by_type["figure"], 15, llm)
        all_rows += rows3
    else:
        cb3 = None

    random.shuffle(all_rows)
    write_csv(all_rows, args.output)

    # ── Summary ─────────────────────────────────────────────────────────────
    by_tool = {}
    for r in all_rows:
        by_tool[r["expected_tool"]] = by_tool.get(r["expected_tool"], 0) + 1
    sections = len({r["expected_section"] for r in all_rows})

    print("\n" + "=" * 50)
    print("=== Generation Summary ===")
    print(f"Track 1 (search_um):       {by_tool.get('search_um', 0)} questions")
    print(f"Track 2 (register_lookup): {by_tool.get('register_lookup', 0)} questions")
    print(f"Track 3 (get_figure):      {by_tool.get('get_figure', 0)} questions")
    print(f"Total:                     {len(all_rows)} questions")
    print(f"Unique sections covered:   {sections}")
    if cb1 and hasattr(cb1, "total_tokens"):
        print(f"Total tokens used:         {cb1.total_tokens:,}")
        print(f"Estimated cost:            ${cb1.total_cost:.4f}")
    print(f"Output:                    {args.output}")
    print("=" * 50)


if __name__ == "__main__":
    main()
