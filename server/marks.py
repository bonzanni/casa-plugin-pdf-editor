"""Writing on the page: text, ticks, crosses, strikes, placeholder boxes, form field values.

The assistant decides every mark and its position; this module draws exactly
those marks and reports, per mark and per field, whether it was applied. Input
comes straight from the assistant's JSON, so anything malformed comes back as a
report entry, never as an exception.

Positions are in displayed points (see ``page_facts``); every conversion to the
unrotated coordinates PyMuPDF draws in goes through ``page_facts.to_page`` and
``page_facts.to_page_rect``.
"""
from __future__ import annotations

import math
import os

import pymupdf

from page_facts import font_alias, radio_buttons, to_displayed, to_page, to_page_rect

_FONT_FILE = os.path.join(os.path.dirname(__file__), "fonts", "DejaVuSans.ttf")
_FONT_NAME = "dvs"
_FONT = pymupdf.Font(fontfile=_FONT_FILE)
_BLACK = (0, 0, 0)
_GREY = (0.5, 0.5, 0.5)
_TEXT_SIZE = 11
_MIN_SIZE = 6
_MARK_SIZE = 10
_STRIKE_WIDTH = 0.8
_BOX_WIDTH = 1
_WHAT_MAX = 40
_KINDS = ("text", "tick", "cross", "strike", "placeholder")
_KEYS = {
    "text": ("x", "y"), "tick": ("x", "y"), "cross": ("x", "y"),
    "strike": ("x0", "y0", "x1", "y1"), "placeholder": ("x", "y", "width", "height"),
}


class _Problem(Exception):
    """A mechanical problem with one mark or field; its message is the report's sentence."""


def _short(text: str) -> str:
    return text if len(text) <= _WHAT_MAX else text[:_WHAT_MAX - 1] + "…"


def _number(mark: dict, key: str) -> float:
    if key not in mark:
        raise _Problem(f"it has no '{key}'.")
    v = mark[key]
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v):
        raise _Problem(f"'{key}' must be a number, not {v!r}.")
    return float(v)


def _positive(mark: dict, key: str, default: float) -> float:
    if key not in mark or mark[key] is None:
        return default
    v = _number(mark, key)
    if v <= 0:
        raise _Problem(f"'{key}' must be greater than 0, not {mark[key]!r}.")
    return v


def _describe(i: int, mark) -> str:
    if not isinstance(mark, dict) or mark.get("kind") not in _KINDS:
        kind = mark.get("kind") if isinstance(mark, dict) else None
        return f"mark {i}" + (f" (kind {kind!r})" if kind is not None else "")
    kind = mark["kind"]
    what = f"text {_short(mark['text'])!r}" if kind == "text" and isinstance(mark.get("text"), str) else kind
    page = mark.get("page")
    if isinstance(page, int) and not isinstance(page, bool):
        what += f" on page {page}"
    return what


def _page(doc: pymupdf.Document, mark: dict) -> pymupdf.Page:
    page = mark.get("page")
    if "page" not in mark:
        raise _Problem("it has no 'page'.")
    if isinstance(page, bool) or not isinstance(page, int):
        raise _Problem(f"'page' must be a whole number, not {page!r}.")
    if not 1 <= page <= doc.page_count:
        raise _Problem(f"page {page} does not exist; this document has pages 1 to {doc.page_count}.")
    return doc[page - 1]


def _inside(page: pymupdf.Page, x0: float, y0: float, x1: float, y1: float) -> None:
    """Refuse a mark whose displayed bounding box is not wholly on the displayed page."""
    pr = page.rect
    if x0 < pr.x0 or y0 < pr.y0 or x1 > pr.x1 or y1 > pr.y1:
        raise _Problem(f"part of it ({x0:.1f}, {y0:.1f}, {x1:.1f}, {y1:.1f}) lies outside the page, "
                       f"which is {pr.width:.1f} x {pr.height:.1f} points as displayed.")


def _text(page: pymupdf.Page, mark: dict, x: float, y: float) -> None:
    text = mark.get("text")
    if not isinstance(text, str) or not text.strip():
        raise _Problem("'text' must be a non-empty string.")
    if "\n" in text or "\r" in text:
        raise _Problem("the text has a line break; use one text mark per line.")
    missing = "".join(dict.fromkeys(c for c in text if not _FONT.has_glyph(ord(c))))
    if missing:
        raise _Problem(f"the font has no glyph for {', '.join(repr(c) for c in missing)}; "
                       f"leave those characters out or replace them.")
    size = _positive(mark, "size", _TEXT_SIZE)
    if size < _MIN_SIZE:
        raise _Problem(f"size {size:g} pt is below the {_MIN_SIZE} pt floor; text that small is not readable.")
    width = _FONT.text_length(text, fontsize=size)
    if mark.get("max_width") is not None:
        max_width = _positive(mark, "max_width", 0)
        if width > max_width:
            size = size * max_width / width
            if size < _MIN_SIZE:
                raise _Problem(f"the text does not fit in {max_width:g} points even at {_MIN_SIZE} pt; "
                               f"it needs {_FONT.text_length(text, fontsize=_MIN_SIZE):.1f} points.")
            width = _FONT.text_length(text, fontsize=size)
    _inside(page, x, y - _FONT.ascender * size, x + width, y - _FONT.descender * size)
    page.insert_text(to_page(page, x, y), text, fontname=font_alias(page, _FONT_NAME), fontfile=_FONT_FILE,
                     fontsize=size, color=_BLACK, rotate=page.rotation)


def _stroke(page: pymupdf.Page, lines: list[list[tuple[float, float]]], width: float, colour) -> None:
    shape = page.new_shape()
    for points in lines:
        shape.draw_polyline([to_page(page, px, py) for px, py in points])
    shape.finish(color=colour, width=width, closePath=False, lineCap=1, lineJoin=1)
    shape.commit()


def _draw(page: pymupdf.Page, mark: dict) -> None:
    kind = mark["kind"]
    v = [_number(mark, k) for k in _KEYS[kind]]
    if kind == "text":
        _text(page, mark, *v)
    elif kind in ("tick", "cross"):
        x, y = v
        s = _positive(mark, "size", _MARK_SIZE)
        _inside(page, x - s / 2, y - s / 2, x + s / 2, y + s / 2)
        h = s * 0.4   # strokes stay inside the s-square, round caps included
        if kind == "tick":
            lines = [[(x - h, y), (x - h / 4, y + h * 0.75), (x + h, y - h * 0.9)]]
        else:
            lines = [[(x - h, y - h), (x + h, y + h)], [(x - h, y + h), (x + h, y - h)]]
        _stroke(page, lines, s / 10, _BLACK)
    elif kind == "strike":
        x0, y0, x1, y1 = v
        _inside(page, min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        _stroke(page, [[(x0, y0), (x1, y1)]], _STRIKE_WIDTH, _BLACK)
    else:
        x, y, w, h = v
        if w <= 0 or h <= 0:
            raise _Problem("'width' and 'height' must be greater than 0.")
        _inside(page, x, y, x + w, y + h)
        shape = page.new_shape()
        shape.draw_rect(to_page_rect(page, x, y, x + w, y + h))
        shape.finish(color=_GREY, width=_BOX_WIDTH)
        shape.commit()


def _mark(doc: pymupdf.Document, mark) -> int:
    if not isinstance(mark, dict):
        raise _Problem("a mark must be an object with a 'kind'.")
    if mark.get("kind") not in _KINDS:
        raise _Problem(f"unknown kind {mark.get('kind')!r}; the kinds are {', '.join(_KINDS)}.")
    page = _page(doc, mark)
    _draw(page, mark)
    return page.number + 1


def _option_values(widget) -> dict[str, str]:
    """Each acceptable value of a choice field mapped to the value to store (export value)."""
    out = {}
    for o in widget.choice_values or ():
        if isinstance(o, str):
            out[o] = o
        else:
            for name in o:
                out.setdefault(str(name), str(o[0]))
    return out


def _cp1252(c: str) -> bool:
    try:
        c.encode("cp1252")
    except UnicodeEncodeError:
        return False
    return True


def _shown_alone(doc: pymupdf.Document, widget, value) -> str:
    """The text a field's widget shows once set to ``value`` and flattened (as signing does),
    with nothing else that could add to it: on a throwaway copy of ``doc`` the page's own
    content is emptied and every other widget and annotation on that page removed."""
    trial = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
    try:
        pno = widget.parent.number
        page = trial[pno]
        for xref in page.get_contents():
            trial.update_stream(xref, b"")
        for annot in list(page.annots()):
            page.delete_annot(annot)
        for other in [w for w in page.widgets() if w.xref != widget.xref]:
            page.delete_widget(other)
        target = next(w for w in page.widgets() if w.xref == widget.xref)
        target.field_value = value
        target.update()
        trial.bake()
        flat = pymupdf.open(stream=trial.tobytes(), filetype="pdf")   # reopened: a loaded page keeps its old content
        try:
            return " ".join(flat[pno].get_text(clip=widget.rect).split())
        finally:
            flat.close()
    finally:
        trial.close()


def _squash(text: str) -> str:
    return "".join(text.split())


def _field(doc: pymupdf.Document, widgets: list, name: str, value) -> set[int]:
    w0 = widgets[0]
    t = w0.field_type
    if t == pymupdf.PDF_WIDGET_TYPE_SIGNATURE:
        raise _Problem("this is a digital-signature field; it cannot be filled in.")
    if t == pymupdf.PDF_WIDGET_TYPE_BUTTON:
        raise _Problem("this is a push button; it has no value to set.")
    if t == pymupdf.PDF_WIDGET_TYPE_CHECKBOX:
        if not isinstance(value, bool):
            raise _Problem(f"a checkbox takes true or false, not {value!r}.")
        targets = [(w, value) for w in widgets]
    elif t == pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON:
        states = radio_buttons(widgets)
        if not isinstance(value, str) or value not in states:
            raise _Problem(f"{value!r} is not one of this radio group's choices: "
                           f"{', '.join(str(s) for s in states)}.")
        targets = [(states[value], True)]
    elif t in (pymupdf.PDF_WIDGET_TYPE_COMBOBOX, pymupdf.PDF_WIDGET_TYPE_LISTBOX):
        options = _option_values(w0)
        if not isinstance(value, str) or value not in options:
            raise _Problem(f"{value!r} is not one of its options: {', '.join(options)}.")
        targets = [(w, options[value]) for w in widgets]
    elif t == pymupdf.PDF_WIDGET_TYPE_TEXT:
        if not isinstance(value, str):
            raise _Problem(f"a text field takes a string, not {value!r}.")
        if value == "":
            raise _Problem("clearing a field is not supported; use undo_last_write.")
        bad = "".join(dict.fromkeys(c for c in value if not _cp1252(c)))
        if bad:   # the field's own font (Helvetica, WinAnsi) draws these as nothing or garbage
            raise _Problem(f"this field's font cannot show {', '.join(repr(c) for c in bad)}; "
                           f"write it as a text mark over the field instead.")
        targets = [(w, value) for w in widgets]
    else:
        raise _Problem(f"fields of type {w0.field_type_string} are not supported.")
    if t in (pymupdf.PDF_WIDGET_TYPE_TEXT, pymupdf.PDF_WIDGET_TYPE_COMBOBOX):
        # ok means: once flattened (signing flattens), the field's own text is its whole value.
        # The appearance drops what does not fit. This is not a proof of legibility (a field too
        # low can clip letters): the report gives the field's page and rect so the assistant can
        # look. A listbox shows its options rather than its value, so it is not checked.
        w, v = targets[0]
        shown = _shown_alone(doc, w, v)
        if _squash(shown) != _squash(v):
            raise _Problem(f"the value does not fit field {name!r} (only {shown!r} would show); "
                           f"shorten it or write it as a text mark.")
    for w, v in targets:
        w.field_value = v
        w.update()
    return {w.parent.number + 1 for w, _ in targets}


def _where(widgets: list | None) -> dict:
    """The field's first widget: 1-based page and displayed rectangle, for the assistant to look at."""
    if not widgets:
        return {"page": None, "rect": None}
    w = widgets[0]
    r = to_displayed(w.parent, w.rect)
    return {"page": w.parent.number + 1, "rect": [round(v, 1) for v in (r.x0, r.y0, r.x1, r.y1)]}


def apply(pdf: bytes, marks: list[dict], fields: dict[str, object]) -> tuple[bytes, list[dict], list[int]]:
    """Draw ``marks`` and set ``fields`` on a copy of ``pdf``.

    Returns the new PDF, one report entry per mark then per field
    (``{"ok", "what", "problem"}``), and the sorted 1-based pages touched.
    The input bytes are never modified.
    """
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        report: list[dict] = []
        touched: set[int] = set()

        def run(what: str, action) -> None:
            try:
                touched.update(action())
            except _Problem as e:
                report.append({"ok": False, "what": what, "problem": str(e)})
            except Exception as e:  # PyMuPDF refused something the checks above let through
                report.append({"ok": False, "what": what, "problem": f"it could not be applied: {e}."})
            else:
                report.append({"ok": True, "what": what, "problem": None})

        if isinstance(marks, dict):
            marks = [marks]
        if marks is not None and not isinstance(marks, list):
            report.append({"ok": False, "what": "marks", "problem": "'marks' must be a list of marks."})
            marks = []
        for i, mark in enumerate(marks or [], start=1):
            run(_describe(i, mark), lambda mark=mark: {_mark(doc, mark)})

        if fields is not None and not isinstance(fields, dict):
            report.append({"ok": False, "what": "fields", "problem": "'fields' must map field names to values."})
            fields = {}
        if fields:
            pages = [doc[n] for n in range(doc.page_count)]   # widgets hold weak references to their page
            by_name: dict[str, list] = {}
            for page in pages:
                for w in page.widgets():
                    by_name.setdefault(w.field_name, []).append(w)
            for name, value in fields.items():
                def fill(name=name, value=value):
                    if name not in by_name:
                        known = ", ".join(by_name) or "none; this document has no form fields"
                        raise _Problem(f"there is no field named {name!r}; the fields are: {known}.")
                    return _field(doc, by_name[name], name, value)
                run(f"field {_short(str(name))!r}", fill)
                report[-1].update(_where(by_name.get(name)))

        return _save(doc), report, sorted(touched)
    finally:
        doc.close()


def _save(doc: pymupdf.Document) -> bytes:
    """The document's bytes with its embedded fonts cut down to the glyphs it uses: every text
    mark embeds the whole bundled font afresh (about 380 KB). Subsetting runs on a copy, so a
    PDF MuPDF cannot subset is saved whole rather than half-changed."""
    whole = doc.tobytes(garbage=3, deflate=True)
    try:
        copy = pymupdf.open(stream=whole, filetype="pdf")
        try:
            copy.subset_fonts()
            return copy.tobytes(garbage=3, deflate=True)
        finally:
            copy.close()
    except Exception:
        return whole
