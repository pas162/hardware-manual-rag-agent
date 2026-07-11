"""
Extract figures from a PDF UM.

Strategy:
  1. Find true caption labels on each page — text blocks that START with "Figure X.Y"
     followed by a title (not a verb like "shows/see/refer").
  2. For each caption, compute the bounding box of all vector drawings on that page
     that sit ABOVE the caption line. This captures the figure frame regardless of
     whether it is a single rect path or many individual line segments.
  3. Crop that drawing-bbox region + the caption line and save as PNG.

This correctly handles vector figures (flowcharts, block diagrams, timing diagrams)
which are invisible to get_images().

Output:
  data/figures/{doc_id}/p{page}_{figure_id_slug}.png
  data/parsed/figures.jsonl
"""

import json
import re
from pathlib import Path

import pymupdf

PAGE_RENDER_DPI = 150
MIN_DRAWING_HEIGHT_PT = 30   # ignore tiny drawing clusters (page header lines etc.)
PAGE_HEADER_Y = 75           # pt — ignore drawings above this (page header / rule line)

# Caption label: Figure ID at START of block, followed by whitespace/newline then title.
# Must NOT be followed immediately by a verb (cross-reference sentence).
_RE_CAPTION = re.compile(
    r"^Fig(?:ure)?\.?\s+(\d+\.\d+(?:\.\d+)?)"   # "Figure X.Y" at start
    r"\s*\n",                                      # caption title is on the NEXT line
    re.IGNORECASE,
)


def _slug(figure_id: str) -> str:
    s = re.sub(r"[Ff]ig(?:ure)?\.?\s*", "fig", figure_id)
    s = re.sub(r"[^a-zA-Z0-9]", "_", s)
    return s.strip("_").lower()


def _find_captions_on_page(page_blocks: list[dict]) -> list[dict]:
    """Return true figure caption labels — blocks starting with 'Figure X.Y <title>'."""
    found = []
    for block in page_blocks:
        text = block.get("text", "").strip()
        m = _RE_CAPTION.match(text)
        if not m:
            continue
        if len(text) > 300:  # captions are short; skip long paragraphs
            continue
        figure_id = f"Figure {m.group(1)}"
        found.append({
            "figure_id": figure_id,
            "caption": text,
            "caption_y":  block["bbox"][1],
            "caption_y1": block["bbox"][3],
        })
    return found


def _find_captions_from_pymupdf(page: "pymupdf.Page") -> list[dict]:
    """Fallback caption finder using raw PyMuPDF blocks — used when pages.jsonl has no
    entries for this page (e.g. figure-only pages with no prose text)."""
    found = []
    for b in page.get_text("blocks"):
        x0, y0, x1, y1, text, *_ = b
        text = text.strip()
        m = _RE_CAPTION.match(text)
        if not m:
            continue
        if len(text) > 300:
            continue
        figure_id = f"Figure {m.group(1)}"
        found.append({
            "figure_id": figure_id,
            "caption": text,
            "caption_y":  y0,
            "caption_y1": y1,
        })
    return found


def _drawing_bbox_above(
    page: pymupdf.Page,
    below_y: float,
    above_y: float = PAGE_HEADER_Y,
) -> pymupdf.Rect | None:
    """Return the union bbox of drawings in the vertical band (above_y, below_y).

    *above_y* defaults to PAGE_HEADER_Y but callers should pass the bottom of
    the previous caption (or table) so drawings belonging to earlier content are
    excluded.  Returns None if no qualifying drawings found.
    """
    x0s, y0s, x1s, y1s = [], [], [], []
    for d in page.get_drawings():
        r = d.get("rect") or d.get("bbox")
        if r is None:
            continue
        # Must overlap the band [above_y, below_y]
        if r.y1 <= above_y:
            continue
        if r.y0 >= below_y:
            continue
        x0s.append(r.x0); y0s.append(max(r.y0, above_y))
        x1s.append(r.x1); y1s.append(r.y1)

    if not x0s:
        return None

    combined = pymupdf.Rect(min(x0s), min(y0s), max(x1s), max(y1s))
    if combined.height < MIN_DRAWING_HEIGHT_PT:
        return None
    return combined


def _drawing_cluster_top(page: pymupdf.Page, below_y: float, above_y: float) -> float:
    """Find the top of the drawing cluster that belongs to the figure above *below_y*.

    When a page has a table (top) followed by a figure (bottom), both produce drawings.
    We split them by finding the largest vertical gap between drawing groups.
    The figure cluster is the last (lowest) group; we return its top y.

    Returns above_y if no drawings found (caller falls back to other logic).
    """
    rects = []
    for d in page.get_drawings():
        r = d.get("rect") or d.get("bbox")
        if r is None:
            continue
        if r.y1 <= above_y:
            continue
        if r.y0 >= below_y:
            continue
        rects.append((r.y0, r.y1))

    if not rects:
        return above_y

    rects.sort()
    if len(rects) == 1:
        return max(above_y, rects[0][0] - 4)

    # Merge overlapping/adjacent rects into vertical clusters (gap tolerance = 8pt)
    GAP = 8
    clusters = []
    cy0, cy1 = rects[0]
    for ry0, ry1 in rects[1:]:
        if ry0 <= cy1 + GAP:
            cy1 = max(cy1, ry1)
        else:
            clusters.append((cy0, cy1))
            cy0, cy1 = ry0, ry1
    clusters.append((cy0, cy1))

    if len(clusters) == 1:
        return max(above_y, clusters[0][0] - 4)

    # Multiple clusters — figure is the last one; return its top
    return max(above_y, clusters[-1][0] - 4)


def _crop_and_save(
    page: pymupdf.Page,
    caption_info: dict,
    all_captions_info: list[dict],
    out_path: Path,
    page_blocks: list[dict] | None = None,
    dpi: int = PAGE_RENDER_DPI,
) -> tuple[int, int]:
    """Crop figure region (drawings + caption) and save PNG. Returns (w_px, h_px)."""
    page_w = page.rect.width
    page_h = page.rect.height
    caption_y  = caption_info["caption_y"]
    caption_y1 = caption_info["caption_y1"]

    # Hard floor: bottom of the previous caption on this page.
    prev_caption_y1s = [c["caption_y1"] for c in all_captions_info if c["caption_y"] < caption_y - 5]
    above_y = max(prev_caption_y1s, default=PAGE_HEADER_Y)

    # Try drawing-bbox approach first
    draw_bbox = _drawing_bbox_above(page, caption_y, above_y=above_y)

    if draw_bbox is not None:
        # Find the top of the last drawing cluster — handles table-above-figure by
        # splitting drawing groups on the largest vertical gap.
        cluster_top = _drawing_cluster_top(page, caption_y, above_y)

        # Also find the bottom of the last prose/heading text block that ends
        # strictly before cluster_top. This trims prose that sits between above_y
        # and the figure (e.g. section heading + cross-reference paragraph).
        if page_blocks is not None:
            last_text_y1 = above_y
            for b in page_blocks:
                by1 = b["bbox"][3]
                text = b.get("text", "").strip()
                if not text or _RE_CAPTION.match(text):
                    continue
                # Only count blocks that end before the figure drawings start
                if by1 < cluster_top - 2:
                    last_text_y1 = max(last_text_y1, by1)
            crop_y0 = max(cluster_top, last_text_y1 + 2)
        else:
            crop_y0 = max(cluster_top, above_y)
        crop_y1 = min(page_h, caption_y1 + 6)
    else:
        # No drawings found — fall back to text-boundary crop
        crop_y0 = above_y + 4
        crop_y1 = caption_y1 + 6

    if crop_y1 - crop_y0 < MIN_DRAWING_HEIGHT_PT:
        # Last resort: half-page around caption
        crop_y0 = max(0, caption_y - page_h * 0.45)
        crop_y1 = min(page_h, caption_y1 + 6)

    clip = pymupdf.Rect(0, crop_y0, page_w, crop_y1)
    mat = pymupdf.Matrix(dpi / 72, dpi / 72)
    pix = page.get_pixmap(matrix=mat, alpha=False, clip=clip)
    pix.save(str(out_path))
    return pix.width, pix.height, crop_y0, crop_y1


def parse_figures(
    pdf_path: str | Path,
    doc_id: str,
    pages_jsonl: Path,
    output_jsonl: Path,
    figures_dir: Path,
    cache_path: Path | None = None,
    openai_client=None,
) -> int:
    """Extract figure images and pair with PDF captions. Returns number of figures emitted."""
    pdf_path = Path(pdf_path)
    out_dir = Path(figures_dir) / doc_id
    out_dir.mkdir(parents=True, exist_ok=True)
    output_jsonl = Path(output_jsonl)
    output_jsonl.parent.mkdir(parents=True, exist_ok=True)

    page_blocks: dict[int, list[dict]] = {}
    with Path(pages_jsonl).open(encoding="utf-8") as f:
        for line in f:
            b = json.loads(line)
            page_blocks.setdefault(b["page"], []).append(b)

    doc = pymupdf.open(str(pdf_path))
    count = 0
    seen: dict[str, int] = {}

    total_pages = len(doc)
    with output_jsonl.open("w", encoding="utf-8") as fout:
        for page_num in range(total_pages):
            if page_num % 50 == 0:
                print(f"    page {page_num + 1}/{total_pages} ({page_num * 100 // total_pages}%)  figures so far: {count}", flush=True)
            page_1 = page_num + 1
            blocks = page_blocks.get(page_1, [])
            captions = _find_captions_on_page(blocks)
            page = doc[page_num]
            # Fallback: if pages.jsonl has no blocks for this page (figure-only pages),
            # scan PyMuPDF raw blocks directly so we still detect captions and create
            # figure zones that prevent false-positive table detections.
            if not captions:
                captions = _find_captions_from_pymupdf(page)
            if not captions:
                continue

            section_path = blocks[0].get("section_path") if blocks else None

            for cap in captions:
                figure_id = cap["figure_id"]
                occ = seen.get(figure_id, 0)
                seen[figure_id] = occ + 1
                suffix = f"_{occ}" if occ > 0 else ""
                img_filename = f"p{page_1}_{_slug(figure_id)}{suffix}.png"
                img_path = out_dir / img_filename

                crop_y0, crop_y1 = 0.0, 0.0
                try:
                    width, height, crop_y0, crop_y1 = _crop_and_save(page, cap, captions, img_path, page_blocks=blocks)
                except Exception:
                    try:
                        mat = pymupdf.Matrix(PAGE_RENDER_DPI / 72, PAGE_RENDER_DPI / 72)
                        pix = page.get_pixmap(matrix=mat, alpha=False)
                        pix.save(str(img_path))
                        width, height = pix.width, pix.height
                    except Exception:
                        width, height = 0, 0

                relative_img_path = (
                    str(Path("data/figures") / doc_id / img_filename).replace("\\", "/")
                )
                record = {
                    "page": page_1,
                    "figure_id": figure_id,
                    "caption": cap["caption"],
                    "caption_y": cap["caption_y"],
                    "crop_y0": round(crop_y0, 1),
                    "crop_y1": round(crop_y1, 1),
                    "vlm_type": "",
                    "vlm_summary": "",
                    "labels_seen": [],
                    "image_path": relative_img_path,
                    "section_path": section_path,
                    "width": width,
                    "height": height,
                }
                fout.write(json.dumps(record, ensure_ascii=False) + "\n")
                count += 1

    doc.close()
    paired = sum(1 for line in output_jsonl.open(encoding="utf-8") if json.loads(line).get("figure_id"))
    print(f"Figures extracted: {count}, paired with figure_id: {paired} ({100 if count else 0:.0f}%)")
    return count


if __name__ == "__main__":
    registry = json.loads(Path("data/registry.json").read_text())
    doc_info = registry[0]
    n = parse_figures(
        pdf_path=Path(doc_info["path"]),
        doc_id=doc_info["doc_id"],
        pages_jsonl=Path("data/parsed/pages.jsonl"),
        output_jsonl=Path("data/parsed/figures.jsonl"),
        figures_dir=Path("data/figures"),
    )
    print(f"\nDone. {n} figure records written to data/parsed/figures.jsonl")
