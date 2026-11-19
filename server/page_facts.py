"""What the assistant looks at: a page image with a ruler, and mechanical facts.

Every coordinate this module reports or accepts is in PDF points, origin at the
top-left of the page as displayed (the visible CropBox area, after rotation),
x right, y down. PyMuPDF's reading and drawing calls work in unrotated page
coordinates; the three helpers below are the only place that converts.

The facts are measurements only: text lines, drawn strokes and small drawn
rectangles filtered by size, and form fields. Nothing here decides what any of
it means.
"""
from __future__ import annotations

import math
import os

import pymupdf

SCALE = 1.5

_FONT = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")
_RULER_STEP = 50
_RULER_COLOUR = (0.0, 0.35, 0.85)
_RULER_FONT_PX = 8
_TICK_PX = 5
_MIN_LINE = 20
_BOX_MIN, _BOX_MAX = 6, 20
_FIELD_TYPES = {
    "Text": "text", "CheckBox": "checkbox", "ComboBox": "combobox", "ListBox": "listbox",
    "RadioButton": "radiobutton", "Signature": "signature", "Button": "button",
}
_NOTE = ("Coordinates are PDF points from the top-left corner of the page as displayed, "
         "x to the right, y down.")


def to_page(page: pymupdf.Page, x: float, y: float) -> pymupdf.Point:
    """Displayed coordinates to the unrotated coordinates reading and drawing calls take."""
    return pymupdf.Point(x, y) * page.derotation_matrix


def to_page_rect(page: pymupdf.Page, x0: float, y0: float, x1: float, y1: float) -> pymupdf.Rect:
    return (pymupdf.Rect(x0, y0, x1, y1) * page.derotation_matrix).normalize()


def to_displayed(page: pymupdf.Page, obj):
    """Unrotated page coordinates (a Point or Rect) to displayed coordinates."""
    if isinstance(obj, pymupdf.Point):
        return obj * page.rotation_matrix
    return (pymupdf.Rect(obj) * page.rotation_matrix).normalize()


def _open_page(pdf: bytes, page: int) -> tuple[pymupdf.Document, pymupdf.Page]:
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    if not 1 <= page <= doc.page_count:
        n = doc.page_count
        doc.close()
        raise ValueError(f"Page {page} does not exist; this document has pages 1 to {n}.")
    return doc, doc[page - 1]


def _r(v: float) -> float:
    return round(float(v), 1)


def _box(rect: pymupdf.Rect) -> dict:
    return {"x0": _r(rect.x0), "y0": _r(rect.y0), "x1": _r(rect.x1), "y1": _r(rect.y1)}


def _text(page: pymupdf.Page) -> list[dict]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            spans = line["spans"]
            text = "".join(s["text"] for s in spans)
            if not spans or not text.strip():
                continue
            baseline = to_displayed(page, pymupdf.Point(spans[0]["origin"])).y
            out.append({"text": text, **_box(to_displayed(page, line["bbox"])),
                        "baseline": _r(baseline), "size": _r(spans[0]["size"])})
    return out


def _drawings(page: pymupdf.Page) -> tuple[list[dict], list[dict]]:
    lines, boxes = [], []
    for path in page.get_drawings():
        for item in path["items"]:
            kind = item[0]
            if kind == "l" and "s" in (path.get("type") or ""):
                p1, p2 = item[1], item[2]
                if abs(p2 - p1) >= _MIN_LINE:
                    lines.append(_box(to_displayed(page, pymupdf.Rect(p1, p2).normalize())))
            elif kind in ("re", "qu"):
                rect = to_displayed(page, item[1].rect if kind == "qu" else item[1])
                if _BOX_MIN <= rect.width <= _BOX_MAX and _BOX_MIN <= rect.height <= _BOX_MAX:
                    boxes.append(_box(rect))
    return lines, boxes


def _fields(page: pymupdf.Page) -> list[dict]:
    out = []
    for w in page.widgets():
        kind = _FIELD_TYPES.get(w.field_type_string, w.field_type_string.lower())
        value = w.field_value
        if kind == "checkbox":
            value = value not in (None, "", "Off", False)
        out.append({"name": w.field_name, "type": kind, "value": value,
                    "options": list(w.choice_values or []),
                    **_box(to_displayed(page, w.rect))})
    return out


def facts(pdf: bytes, page: int) -> dict:
    """Mechanical facts about one page (1-based), in displayed coordinates, rounded to 0.1."""
    doc, pg = _open_page(pdf, page)
    try:
        text = _text(pg)
        lines, boxes = _drawings(pg)
        note = _NOTE if text else _NOTE[:-1] + "; this page has no text layer."
        return {"page": page, "pages": doc.page_count,
                "width": _r(pg.rect.width), "height": _r(pg.rect.height),
                "text": text, "lines": lines, "boxes": boxes, "fields": _fields(pg),
                "note": note}
    finally:
        doc.close()


def _ruler_values(lo: float, hi: float) -> list[int]:
    first = math.ceil(lo / _RULER_STEP) * _RULER_STEP
    return [v for v in range(first, math.floor(hi) + 1, _RULER_STEP) if v > 0]


def _with_ruler(pix: pymupdf.Pixmap, origin: tuple[float, float], scale: float) -> bytes:
    """PNG of ``pix`` with tick marks and point values along its top and left edges.

    The image is placed 1:1 on a scratch page (pixels as points, measured to
    rasterise back unchanged) and the ruler drawn over it; the scratch page is
    discarded. The PDF itself is never touched.
    """
    sheet = pymupdf.open()
    try:
        canvas = sheet.new_page(width=pix.width, height=pix.height)
        canvas.insert_image(canvas.rect, pixmap=pix)
        ox, oy = origin
        width, height = pix.width / scale, pix.height / scale
        shape = canvas.new_shape()
        for v in _ruler_values(ox, ox + width):
            px = (v - ox) * scale
            shape.draw_line((px, 0), (px, _TICK_PX))
            canvas.insert_text((px + 1.5, _TICK_PX + _RULER_FONT_PX - 1), str(v),
                               fontsize=_RULER_FONT_PX, fontname="dejavu", fontfile=_FONT,
                               color=_RULER_COLOUR)
        for v in _ruler_values(oy, oy + height):
            py = (v - oy) * scale
            shape.draw_line((0, py), (_TICK_PX, py))
            canvas.insert_text((1, py + _RULER_FONT_PX), str(v),
                               fontsize=_RULER_FONT_PX, fontname="dejavu", fontfile=_FONT,
                               color=_RULER_COLOUR)
        shape.finish(color=_RULER_COLOUR, width=1)
        shape.commit()
        return canvas.get_pixmap().tobytes("png")
    finally:
        sheet.close()


def render(pdf: bytes, page: int, *, region: tuple[float, float, float, float] | None = None) -> bytes:
    """PNG of one page (1-based) at ``SCALE``, or of ``region`` (displayed points) at ``SCALE * 2``,
    with a 50 pt ruler drawn on the image."""
    doc, pg = _open_page(pdf, page)
    try:
        scale = SCALE if region is None else SCALE * 2
        clip = None if region is None else pymupdf.Rect(region)
        pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip)
        return _with_ruler(pix, (pix.x / scale, pix.y / scale), scale)
    finally:
        doc.close()
