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
_RULER_FONT_PX = 10
_TICK_PX = 5
_MIN_LINE = 20
_BOX_MIN, _BOX_MAX = 6, 20
_BAR_MAX = 3      # a filled rectangle this thin and at least _MIN_LINE long is reported as a line
_MARK_MAX = 6     # marks this small, in a row with gaps up to _MARK_GAP, are reported as one line
_MARK_GAP = 6
_ROW_TOL = 1
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
    """Unrotated page coordinates to displayed coordinates: a Point or (x, y) gives a Point,
    a Rect or (x0, y0, x1, y1) gives a normalised Rect."""
    if isinstance(obj, pymupdf.Point) or (not isinstance(obj, pymupdf.Rect) and len(obj) == 2):
        return pymupdf.Point(obj) * page.rotation_matrix
    return (pymupdf.Rect(obj) * page.rotation_matrix).normalize()


def font_alias(page: pymupdf.Page, wanted: str) -> str:
    """An unused resource name for a font the plugin is about to embed on ``page``: ``wanted``
    if the page has no font of that name, otherwise ``wanted`` plus the first free number.

    ``insert_text``/``insert_font`` draw with whatever font a page already maps to a name, and
    nothing short of the PDF's own encoding tables says such a font maps characters the way
    ours does, so a font already in the document is never reused, whatever it contains."""
    used = {f[4] for f in page.get_fonts(full=True)}   # (xref, ext, type, basefont, name, ...)
    if wanted not in used:
        return wanted
    n = 1
    while f"{wanted}{n}" in used:
        n += 1
    return f"{wanted}{n}"


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


def _visible(rect: pymupdf.Rect, page_rect: pymupdf.Rect) -> bool:
    """Whether a displayed rectangle shows on the page; a zero-width or zero-height one
    (a line) counts when its segment touches the page."""
    if rect.width > 0 and rect.height > 0:
        return rect.intersects(page_rect)
    return (rect.x0 <= page_rect.x1 and rect.x1 >= page_rect.x0
            and rect.y0 <= page_rect.y1 and rect.y1 >= page_rect.y0)


def _text(page: pymupdf.Page) -> list[dict]:
    out = []
    for block in page.get_text("dict")["blocks"]:
        for line in block.get("lines", ()):
            spans = line["spans"]
            text = "".join(s["text"] for s in spans)
            if not spans or not text.strip():
                continue
            rect = to_displayed(page, line["bbox"])
            if not _visible(rect, page.rect):
                continue
            baseline = to_displayed(page, spans[0]["origin"]).y
            out.append({"text": text, **_box(rect),
                        "baseline": _r(baseline), "size": _r(spans[0]["size"])})
    return out


def _runs(marks: list[pymupdf.Rect], vertical: bool) -> tuple[list[pymupdf.Rect], list[pymupdf.Rect]]:
    """Group marks lying on one horizontal (or vertical) line with small gaps into runs.

    Returns (one displayed line rect per run at least _MIN_LINE long, marks left over).
    Sort and sweep: rows by centre within _ROW_TOL, then split a row at gaps over _MARK_GAP.
    """
    def along(r):   # (start, end) along the run direction
        return (r.y0, r.y1) if vertical else (r.x0, r.x1)

    def across(r):  # centre across the run direction
        return (r.x0 + r.x1) / 2 if vertical else (r.y0 + r.y1) / 2

    rows: list[list[pymupdf.Rect]] = []
    for m in sorted(marks, key=across):
        if rows and across(m) - across(rows[-1][0]) <= _ROW_TOL:
            rows[-1].append(m)
        else:
            rows.append([m])
    runs, rest = [], []
    for row in rows:
        row.sort(key=lambda r: along(r)[0])
        group = [row[0]]
        for m in row[1:] + [None]:
            if m is not None and along(m)[0] - max(along(g)[1] for g in group) <= _MARK_GAP:
                group.append(m)
                continue
            start, end = along(group[0])[0], max(along(g)[1] for g in group)
            if end - start >= _MIN_LINE:
                c = sum(across(g) for g in group) / len(group)
                runs.append(pymupdf.Rect(c, start, c, end) if vertical else pymupdf.Rect(start, c, end, c))
            else:
                rest.extend(group)
            group = [m]
    return runs, rest


def _drawings(page: pymupdf.Page) -> tuple[list[dict], list[dict]]:
    """Drawn strokes, thin bars and runs of small marks at least _MIN_LINE long as lines;
    drawn rectangles with both sides in [_BOX_MIN, _BOX_MAX] as boxes. Size only."""
    lines, boxes, marks = [], [], []
    for path in page.get_drawings():
        items = path["items"]
        if items and all(item[0] == "c" for item in items):   # a dot drawn with curves
            rect = to_displayed(page, path["rect"])
            if rect.width <= _MARK_MAX and rect.height <= _MARK_MAX:
                marks.append(rect)
            continue
        for item in items:
            kind = item[0]
            if kind == "l" and "s" in (path.get("type") or ""):
                p1, p2 = item[1], item[2]
                if abs(p2 - p1) >= _MIN_LINE:
                    lines.append(to_displayed(page, pymupdf.Rect(p1, p2).normalize()))
            elif kind in ("re", "qu"):
                rect = to_displayed(page, item[1].rect if kind == "qu" else item[1])
                short, long = sorted((rect.width, rect.height))
                if short <= _BAR_MAX and long >= _MIN_LINE:
                    c = (rect.y0 + rect.y1) / 2 if rect.width >= rect.height else (rect.x0 + rect.x1) / 2
                    lines.append(pymupdf.Rect(rect.x0, c, rect.x1, c) if rect.width >= rect.height
                                 else pymupdf.Rect(c, rect.y0, c, rect.y1))
                elif long <= _MARK_MAX:
                    marks.append(rect)
                elif _BOX_MIN <= short and long <= _BOX_MAX:
                    boxes.append(rect)
    horizontal, marks = _runs(marks, vertical=False)
    vertical, marks = _runs(marks, vertical=True)
    lines += horizontal + vertical
    boxes += [m for m in marks if _BOX_MIN <= min(m.width, m.height)]
    pr = page.rect
    return ([_box(r) for r in lines if _visible(r, pr)],
            [_box(r) for r in boxes if _visible(r, pr)])


def radio_buttons(widgets) -> dict[str, pymupdf.Widget]:
    """The radio buttons among ``widgets`` by their on-state name (the value that selects each)."""
    out = {}
    for w in widgets:
        if w.field_type == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
            state = w.on_state()
            if isinstance(state, str):
                out.setdefault(state, w)
    return out


def _fields(page: pymupdf.Page) -> list[dict]:
    out = []
    widgets = list(page.widgets())
    for w in widgets:
        kind = _FIELD_TYPES.get(w.field_type_string, w.field_type_string.lower())
        value = w.field_value
        if kind == "checkbox":
            value = value not in (None, "", "Off", False)
        rect = to_displayed(page, w.rect)
        if not _visible(rect, page.rect):
            continue
        if kind == "radiobutton":
            options = list(radio_buttons(x for x in widgets if x.field_name == w.field_name))
        else:
            options = list(w.choice_values or [])
        out.append({"name": w.field_name, "type": kind, "value": value,
                    "options": options,
                    **_box(rect)})
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
            canvas.insert_text((0.5, py + _RULER_FONT_PX), str(v),
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
        clip = None if region is None else pymupdf.Rect(region) & pg.rect
        pix = None
        if clip is None or not clip.is_empty:
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(scale, scale), clip=clip)
        if pix is None or pix.width == 0 or pix.height == 0:
            raise ValueError(f"Region {tuple(region)} lies outside the page; this page is "
                             f"{_r(pg.rect.width)} x {_r(pg.rect.height)} points as displayed.")
        return _with_ruler(pix, (pix.x / scale, pix.y / scale), scale)
    finally:
        doc.close()
