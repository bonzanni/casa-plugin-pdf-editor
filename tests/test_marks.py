import pymupdf

import os

from fixtures import (blank_page, cropped_form, field_form, flat_form, fonts_premapped, has_ink, ink_in, radio_form,
                      raw_span, rotated_field_form, rotated_form)
from marks import apply
from page_facts import facts, to_page_rect

_DEJAVU = pymupdf.Font(fontfile=os.path.join(os.path.dirname(__file__), "..", "server", "fonts", "DejaVuSans.ttf"))


def _drew(src: bytes, out: bytes, rect, page: int = 1, at_least: int = 40) -> bool:
    """``out`` has clearly more ink than ``src`` in the displayed ``rect``."""
    return ink_in(out, page, rect) - ink_in(src, page, rect) >= at_least


def test_text_lands_at_the_baseline_asked_for():
    src = flat_form()
    out, report, pages = apply(src, [{"kind": "text", "page": 1, "x": 205, "y": 600, "text": "A. Example"}], {})
    t = next(t for t in facts(out, 1)["text"] if t["text"] == "A. Example")
    assert abs(t["x0"] - 205) < 1.5 and abs(t["baseline"] - 600) < 1.5
    assert _drew(src, out, (205, 589, 205 + _DEJAVU.text_length("A. Example", 11), 603), at_least=200)
    assert report == [{"ok": True, "what": "text 'A. Example' on page 1", "problem": None}] and pages == [1]

def test_text_outside_latin1_renders_as_glyphs():
    src = flat_form()
    out, _, _ = apply(src, [{"kind": "text", "page": 1, "x": 205, "y": 600, "text": "Łukasz Ćwik"}], {})
    assert any(t["text"] == "Łukasz Ćwik" for t in facts(out, 1)["text"])
    # ink where each non-Latin-1 glyph is (above the dashed line at y 602)
    for before, glyph in (("", "Ł"), ("Łukasz ", "Ć")):
        x = 205 + _DEJAVU.text_length(before, 11)
        assert _drew(src, out, (x, 588, x + _DEJAVU.text_length(glyph, 11), 600.5), at_least=15), glyph

def test_max_width_shrinks_then_refuses():
    long = "A very long name that cannot possibly fit in forty points"
    _, report, _ = apply(flat_form(), [{"kind": "text", "page": 1, "x": 205, "y": 600, "text": long, "max_width": 40}], {})
    assert report[0]["ok"] is False and "fit" in report[0]["problem"]

def test_one_bad_mark_does_not_stop_the_others():
    out, report, _ = apply(flat_form(), [
        {"kind": "text", "page": 1, "x": 5000, "y": 600, "text": "off"},
        {"kind": "strike", "page": 1, "x0": 72, "y0": 746, "x1": 100, "y1": 746}], {})
    assert [r["ok"] for r in report] == [False, True]
    assert len(facts(out, 1)["lines"]) == len(facts(flat_form(), 1)["lines"]) + 1
    assert _drew(flat_form(), out, (72, 745, 100, 747))

def test_mark_on_a_rotated_page_lands_where_displayed():
    out, _, _ = apply(rotated_form(), [{"kind": "text", "page": 1, "x": 100, "y": 100, "text": "HERE"}], {})
    assert _drew(rotated_form(), out, (100, 89, 100 + _DEJAVU.text_length("HERE", 11), 102))
    span = raw_span(out, "HERE")                 # read with pymupdf directly, in unrotated coordinates
    # displayed (100, 100) on a page 841.9 high rotated 90 is unrotated (100, 741.9); upright there means reading direction (0, -1)
    assert abs(span["origin"][0] - 100) < 1.5 and abs(span["origin"][1] - 741.9) < 1.5 and span["dir"] == (0.0, -1.0)

def test_mark_on_a_cropped_page_lands_where_displayed():
    out, _, _ = apply(cropped_form(), [{"kind": "text", "page": 1, "x": 100, "y": 100, "text": "HERE"}], {})
    assert _drew(cropped_form(), out, (100, 89, 100 + _DEJAVU.text_length("HERE", 11), 102))
    doc = pymupdf.open(stream=out, filetype="pdf"); doc[0].set_cropbox(doc[0].mediabox)
    span = raw_span(doc.tobytes(), "HERE")       # with the crop lifted, the mark is 50 right and 100 down
    assert abs(span["origin"][0] - 150) < 1.5 and abs(span["origin"][1] - 200) < 1.5

def test_fields_are_set_and_read_back():
    out, report, _ = apply(field_form(), [], {"name": "A. Example", "agree": True, "colour": "blue", "nope": 1, "colour2": "x"})
    values = {f["name"]: f["value"] for f in facts(out, 1)["fields"]}
    assert values == {"name": "A. Example", "agree": True, "colour": "blue"}
    for rect in ((200, 590, 400, 620), (200, 640, 215, 655), (200, 680, 400, 700)):
        assert _drew(field_form(), out, rect, at_least=20), rect
    assert [r["ok"] for r in report] == [True, True, True, False, False]

def test_field_on_a_rotated_page_is_reported_and_filled_where_displayed():
    # field_form's text field `name` is at Rect(200, 590, 400, 620); rotated 90 on an 841.9 pt high page that displays at (221.9, 200, 251.9, 400)
    f = next(x for x in facts(rotated_field_form(), 1)["fields"] if x["name"] == "name")
    assert all(abs(a - b) < 1 for a, b in zip((f["x0"], f["y0"], f["x1"], f["y1"]), (221.9, 200, 251.9, 400)))
    out, _, _ = apply(rotated_field_form(), [], {"name": "A. Example"})
    assert _drew(rotated_field_form(), out, (221.9, 200, 251.9, 400))
    doc = pymupdf.open(stream=out, filetype="pdf"); doc.bake()
    shown = pymupdf.open(stream=doc.tobytes(), filetype="pdf")[0]
    assert "A. Example" in shown.get_text(clip=to_page_rect(shown, 221.9, 200, 251.9, 400)) and list(shown.widgets()) == []

def test_input_bytes_are_not_changed():
    src = flat_form(); copy = bytes(src); apply(src, [{"kind": "tick", "page": 1, "x": 80, "y": 700}], {})
    assert src == copy


# Beyond the brief: the controller's decisions on malformed input, radio groups and rotated geometry.

def test_malformed_marks_are_reported_and_the_rest_apply():
    out, report, pages = apply(flat_form(), [
        {"kind": "circle", "page": 1, "x": 1, "y": 1},
        {"kind": "tick", "page": 1, "x": "80", "y": 700},
        {"kind": "cross", "page": 1, "y": 700},
        {"kind": "strike", "page": 3, "x0": 72, "y0": 746, "x1": 100, "y1": 746},
        "not a mark",
        {"kind": "tick", "page": 1, "x": 80, "y": 700}], {})
    assert [r["ok"] for r in report] == [False, False, False, False, False, True]
    assert "unknown kind 'circle'" in report[0]["problem"]
    assert "'x' must be a number" in report[1]["problem"]
    assert "no 'x'" in report[2]["problem"]
    assert "page 3 does not exist" in report[3]["problem"]
    assert [r["what"] for r in report][1:4] == ["tick on page 1", "cross on page 1", "strike on page 3"]
    assert pages == [1]

def test_nothing_applied_returns_an_equal_document_and_no_pages():
    src = flat_form()
    out, report, pages = apply(src, [{"kind": "placeholder", "page": 1, "x": 500, "y": 800, "width": 200, "height": 60}],
                               {"nope": "x"})
    assert out is not src and [r["ok"] for r in report] == [False, False] and pages == []
    assert facts(out, 1) == facts(src, 1)
    assert "outside the page" in report[0]["problem"] and report[0]["what"] == "placeholder on page 1"
    assert "no field named 'nope'" in report[1]["problem"] and report[1]["what"] == "field 'nope'"

def test_long_text_is_truncated_in_what():
    long = "x" * 60
    _, report, _ = apply(flat_form(), [{"kind": "text", "page": 1, "x": 72, "y": 100, "text": long}], {})
    assert report[0]["what"] == "text '" + "x" * 39 + "…' on page 1"

def test_max_width_shrinks_text_that_almost_fits():
    out, report, _ = apply(flat_form(), [{"kind": "text", "page": 1, "x": 205, "y": 600, "text": "A. Example",
                                          "max_width": 40}], {})
    t = next(t for t in facts(out, 1)["text"] if t["text"] == "A. Example")
    assert report[0]["ok"] and t["x1"] - t["x0"] <= 41 and 6 <= t["size"] < 11

def test_strike_and_box_on_a_rotated_page_land_where_displayed():
    out, report, _ = apply(rotated_form(), [
        {"kind": "strike", "page": 1, "x0": 100, "y0": 300, "x1": 400, "y1": 300},
        {"kind": "placeholder", "page": 1, "x": 100, "y": 400, "width": 150, "height": 40}], {})
    assert all(r["ok"] for r in report)
    f = facts(out, 1)
    assert any(abs(l["x0"] - 100) < 1 and abs(l["x1"] - 400) < 1 and abs(l["y0"] - 300) < 1 for l in f["lines"])
    assert _drew(rotated_form(), out, (100, 299, 400, 301))
    doc = pymupdf.open(stream=out, filetype="pdf"); page = doc[0]
    from page_facts import to_displayed
    rects = [to_displayed(page, d["rect"]) for d in page.get_drawings() if d.get("color") == (0.5, 0.5, 0.5)]
    assert any(abs(r.x0 - 100) < 1 and abs(r.y0 - 400) < 1 and abs(r.x1 - 250) < 1 and abs(r.y1 - 440) < 1 for r in rects)
    for edge in ((100, 399, 250, 401), (100, 439, 250, 441), (99, 400, 101, 440), (249, 400, 251, 440)):
        assert ink_in(out, 1, edge, below=200) >= 20, edge                  # the grey border shows
    for clear in ((104, 404, 246, 436), (100, 445, 250, 470)):
        assert ink_in(out, 1, clear, below=250) == ink_in(rotated_form(), 1, clear, below=250), clear  # nothing added inside or below

def test_tick_and_cross_draw_ink_inside_their_square():
    # displayed y 400 on rotated_form is blank across x 80..220 (its content sits at displayed y < 270)
    out, report, _ = apply(rotated_form(), [{"kind": "tick", "page": 1, "x": 100, "y": 400, "size": 20},
                                            {"kind": "cross", "page": 1, "x": 200, "y": 400, "size": 20}], {})
    assert all(r["ok"] for r in report)
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    for cx in (100, 200):
        assert has_ink(page.get_pixmap(clip=pymupdf.Rect(cx - 10, 390, cx + 10, 410)))
        assert _drew(rotated_form(), out, (cx - 10, 390, cx + 10, 410))
        around = [pymupdf.Rect(cx - 16, 384, cx + 16, 389), pymupdf.Rect(cx - 16, 411, cx + 16, 416),
                  pymupdf.Rect(cx - 16, 384, cx - 11, 416), pymupdf.Rect(cx + 11, 384, cx + 16, 416)]
        assert not any(has_ink(page.get_pixmap(clip=r)) for r in around)

def test_field_values_of_the_wrong_kind_are_refused():
    _, report, _ = apply(field_form(), [], {"name": 5, "agree": "yes", "colour": "green"})
    assert [r["ok"] for r in report] == [False, False, False]
    assert "string" in report[0]["problem"] and "true or false" in report[1]["problem"]
    assert "red, blue" in report[2]["problem"]


def test_radio_group_takes_the_on_state_of_one_button():
    out, report, _ = apply(radio_form(), [], {"pick": "b"})
    assert report == [{"ok": True, "what": "field 'pick'", "problem": None, "page": 1, "rect": [100.0, 100.0, 115.0, 115.0]}]
    assert [f["value"] for f in facts(out, 1)["fields"]] == ["Off", "b"]
    out, report, _ = apply(out, [], {"pick": "a"})
    assert [f["value"] for f in facts(out, 1)["fields"]] == ["a", "Off"]
    _, report, _ = apply(out, [], {"pick": "c"})
    assert report[0]["ok"] is False and "a, b" in report[0]["problem"]

def test_signature_field_is_refused():
    doc = pymupdf.open(); page = doc.new_page()
    w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_SIGNATURE; w.field_name = "sig"
    w.rect = pymupdf.Rect(100, 100, 300, 140); page.add_widget(w)
    _, report, pages = apply(doc.tobytes(), [], {"sig": "A. Example"})
    assert report[0]["ok"] is False and "digital-signature field" in report[0]["problem"] and pages == []


# Fix round 1: refuse what would not be readable.

def _baked_text(pdf: bytes) -> str:
    doc = pymupdf.open(stream=pdf, filetype="pdf"); doc.bake()
    return pymupdf.open(stream=doc.tobytes(), filetype="pdf")[0].get_text()

def test_text_field_with_accents_is_readable_after_baking():
    out, report, _ = apply(field_form(), [], {"name": "José Müller"})
    assert report[0]["ok"] is True
    assert "José Müller" in _baked_text(out)
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    assert has_ink(page.get_pixmap(clip=pymupdf.Rect(200, 590, 400, 620)), inset=2)

def test_text_field_outside_cp1252_is_refused_and_unchanged():
    out, report, pages = apply(field_form(), [], {"name": "Łukasz Ćwik"})
    assert report[0]["ok"] is False and "'Ł'" in report[0]["problem"] and "text mark" in report[0]["problem"]
    assert pages == []
    before = next(f for f in facts(field_form(), 1)["fields"] if f["name"] == "name")["value"]
    assert next(f for f in facts(out, 1)["fields"] if f["name"] == "name")["value"] == before

def test_clearing_a_text_field_is_refused():
    filled, _, _ = apply(field_form(), [], {"name": "A. Example"})
    out, report, _ = apply(filled, [], {"name": ""})
    assert report[0]["ok"] is False and "undo_last_write" in report[0]["problem"]
    assert next(f for f in facts(out, 1)["fields"] if f["name"] == "name")["value"] == "A. Example"

def test_text_with_characters_the_font_lacks_is_refused():
    out, report, pages = apply(flat_form(), [
        {"kind": "text", "page": 1, "x": 72, "y": 100, "text": "山田 太郎 😀"},
        {"kind": "text", "page": 1, "x": 72, "y": 130, "text": "a\tb\x00c"}], {})
    assert [r["ok"] for r in report] == [False, False] and pages == []
    assert "'山'" in report[0]["problem"] and "'\\t'" in report[1]["problem"] and "'\\x00'" in report[1]["problem"]
    assert facts(out, 1)["text"] == facts(flat_form(), 1)["text"]

def test_text_below_six_points_is_refused():
    _, report, _ = apply(flat_form(), [{"kind": "text", "page": 1, "x": 72, "y": 100, "text": "tiny", "size": 5}], {})
    assert report[0]["ok"] is False and "6 pt" in report[0]["problem"]


# Final review fix wave: values must be wholly visible; bundled fonts get an unused resource name.

def _narrow_field(flags: int = 0, rect=(50, 50, 100, 70)) -> bytes:
    """One text field `f`, 50 x 20 points, font size 14, no MaxLen."""
    doc = pymupdf.open(); page = doc.new_page()
    w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT; w.field_name = "f"
    w.rect = pymupdf.Rect(rect); w.text_fontsize = 14; w.field_flags = flags
    page.add_widget(w)
    return doc.tobytes()

def test_a_text_value_that_would_be_cut_off_is_refused_and_the_field_kept():
    filled, report, _ = apply(_narrow_field(), [], {"f": "ABC"})
    assert report[0]["ok"] is True
    for value in ("ABCDEFGHIJKLMN", "ABCDEFGHZZZZZ"):
        out, report, pages = apply(filled, [], {"f": value})
        assert report[0]["ok"] is False and pages == []
        assert "does not fit field 'f'" in report[0]["problem"] and "'ABCDE'" in report[0]["problem"]
        assert facts(out, 1)["fields"][0]["value"] == "ABC"

def test_a_wrapped_multiline_value_counts_as_visible():
    pdf = _narrow_field(pymupdf.PDF_TX_FIELD_IS_MULTILINE, (50, 50, 150, 120))
    value = "one two three four five six seven eight nine ten"
    out, report, _ = apply(pdf, [], {"f": value})
    assert report[0]["ok"] is True and facts(out, 1)["fields"][0]["value"] == value
    _, report, _ = apply(pdf, [], {"f": value + " eleven twelve thirteen fourteen fifteen sixteen"})
    assert report[0]["ok"] is False

def test_a_choice_whose_text_would_be_cut_off_is_refused():
    doc = pymupdf.open(); page = doc.new_page()
    w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_COMBOBOX; w.field_name = "c"
    w.choice_values = ["red", "a very long blue option"]; w.rect = pymupdf.Rect(50, 50, 100, 70); w.text_fontsize = 12
    page.add_widget(w)
    pdf = doc.tobytes()
    assert [r["ok"] for r in apply(pdf, [], {"c": "red"})[1]] == [True]
    _, report, _ = apply(pdf, [], {"c": "a very long blue option"})
    assert report[0]["ok"] is False and "does not fit" in report[0]["problem"]

def test_a_page_font_named_like_ours_does_not_replace_it():
    mark = [{"kind": "text", "page": 1, "x": 72, "y": 100, "text": "éΩЖ", "size": 20}]
    out, report, _ = apply(fonts_premapped(("dvs",)), mark, {})
    control, _, _ = apply(blank_page(), mark, {})
    assert report[0]["ok"] is True
    assert any(t["text"] == "éΩЖ" for t in facts(out, 1)["text"])
    box = (72, 75, 72 + _DEJAVU.text_length("éΩЖ", 20), 106)
    assert ink_in(control, 1, box) > 100 and ink_in(out, 1, box) == ink_in(control, 1, box)

def test_three_successive_writes_each_get_a_fresh_font_and_all_show():
    pdf = flat_form()
    lines = [("éΩЖ", 100), ("Łukasz Ćwik", 200), ("José Müller", 300)]
    sizes = [len(pdf)]
    for text, y in lines:
        pdf, report, _ = apply(pdf, [{"kind": "text", "page": 1, "x": 72, "y": y, "text": text, "size": 20}], {})
        assert report[0]["ok"] is True
        sizes.append(len(pdf))
    assert {t for t, _ in lines} <= {t["text"] for t in facts(pdf, 1)["text"]}
    for text, y in lines:
        alone, _, _ = apply(flat_form(), [{"kind": "text", "page": 1, "x": 72, "y": y, "text": text, "size": 20}], {})
        box = (72, y - 25, 72 + _DEJAVU.text_length(text, 20), y + 6)
        assert ink_in(pdf, 1, box) == ink_in(alone, 1, box) > 100, text
    page = pymupdf.open(stream=pdf, filetype="pdf")[0]
    assert sorted(f[4] for f in page.get_fonts(full=True) if f[4].startswith("dvs")) == ["dvs", "dvs1", "dvs2"]
    assert all(b - a < 60_000 for a, b in zip(sizes, sizes[1:])), sizes   # fonts subset, not 380 KB each

def test_our_font_file_under_another_mapping_is_not_reused():
    from fixtures import remapped_dejavu
    mark = [{"kind": "text", "page": 1, "x": 72, "y": 100, "text": "ABCDE", "size": 20}]
    out, report, _ = apply(remapped_dejavu(), mark, {})
    control, _, _ = apply(blank_page(), mark, {})
    assert report[0]["ok"] is True and any(t["text"] == "ABCDE" for t in facts(out, 1)["text"])
    box = (72, 75, 72 + _DEJAVU.text_length("ABCDE", 20), 106)
    assert ink_in(control, 1, box) > 100
    shot = [pymupdf.open(stream=p, filetype="pdf")[0].get_pixmap(clip=box).samples for p in (out, control)]
    assert shot[0] == shot[1]

def test_a_font_only_named_like_ours_is_not_reused():
    from fixtures import spoofed_dejavu
    mark = [{"kind": "text", "page": 1, "x": 72, "y": 100, "text": "ΩЖ", "size": 20}]
    out, report, _ = apply(spoofed_dejavu(), mark, {})
    control, _, _ = apply(blank_page(), mark, {})
    assert report[0]["ok"] is True and any(t["text"] == "ΩЖ" for t in facts(out, 1)["text"])
    box = (72, 75, 72 + _DEJAVU.text_length("ΩЖ", 20), 106)
    assert ink_in(control, 1, box) > 100 and ink_in(out, 1, box) == ink_in(control, 1, box)

def _field_over_text(width: float, printed: str) -> bytes:
    """A text field `f` at (50, 50, 50 + width, 70), font size 14, over 2-pt printed text."""
    doc = pymupdf.open(); page = doc.new_page()
    page.insert_text((51, 60), printed, fontsize=2)
    w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT; w.field_name = "f"
    w.rect = pymupdf.Rect(50, 50, 50 + width, 70); w.text_fontsize = 14
    page.add_widget(w)
    return doc.tobytes()

def test_page_text_under_a_field_does_not_count_as_the_value():
    _, report, _ = apply(_field_over_text(35, "ABCDEFGHIJKLMN"), [], {"f": "ABCDEFGHIJKLMN"})
    assert report[0]["ok"] is False and "does not fit field 'f'" in report[0]["problem"]
    out, report, _ = apply(_field_over_text(200, "ABCDEFGHIJKLMN"), [], {"f": "ABCDEFGHIJKLMN"})
    assert report[0]["ok"] is True
    again, report, _ = apply(out, [], {"f": "ABCDEFGHIJKLMN"})   # setting the same value again
    assert report[0]["ok"] is True

def test_field_entries_say_where_to_look():
    for pdf in (field_form(), rotated_field_form()):
        _, report, _ = apply(pdf, [], {"name": "A. Example", "agree": True, "colour": "green", "nope": "x"})
        boxes = {f["name"]: [f["x0"], f["y0"], f["x1"], f["y1"]] for f in facts(pdf, 1)["fields"]}
        for entry, name in zip(report[:3], ("name", "agree", "colour")):
            assert entry["page"] == 1 and entry["rect"] == boxes[name]
        assert report[3]["page"] is None and report[3]["rect"] is None

def _two_sources(printed: str, at, field_rect, size: float = 12) -> bytes:
    """Text `printed` at 8 pt at `at`, and a text field `f` at `field_rect` with font size `size`."""
    doc = pymupdf.open(); page = doc.new_page()
    page.insert_text(at, printed, fontsize=8)
    w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT; w.field_name = "f"
    w.rect = pymupdf.Rect(field_rect); w.text_fontsize = size
    page.add_widget(w)
    return doc.tobytes()

def test_printed_text_cannot_complete_a_cut_off_value():
    src = _two_sources("AB", (101, 110), (100, 100, 118, 120))
    out, report, pages = apply(src, [], {"f": "ABAB"})
    assert report[0]["ok"] is False and pages == []
    assert "only 'AB' would show" in report[0]["problem"]
    assert facts(out, 1)["fields"][0]["value"] == facts(src, 1)["fields"][0]["value"]

def test_another_field_holding_the_value_does_not_count():
    doc = pymupdf.open(); page = doc.new_page()
    for name, rect in (("g", (50, 50, 300, 70)), ("f", (50, 50, 85, 70))):
        w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT; w.field_name = name
        w.rect = pymupdf.Rect(rect); w.text_fontsize = 14
        page.add_widget(w)
    value = "ABCDEFGHIJKLMN"
    filled, report, _ = apply(doc.tobytes(), [], {"g": value})
    assert report[0]["ok"] is True
    out, report, _ = apply(filled, [], {"f": value})
    assert report[0]["ok"] is False and "does not fit field 'f'" in report[0]["problem"]
    assert {f["name"]: f["value"] for f in facts(out, 1)["fields"]}["g"] == value

def test_a_field_on_a_rotated_page_passes_the_check_and_a_cut_off_one_does_not():
    out, report, _ = apply(rotated_field_form(), [], {"name": "A. Example"})
    assert report[0]["ok"] is True
    doc = pymupdf.open(stream=_narrow_field(), filetype="pdf"); doc[0].set_rotation(90)
    turned = doc.tobytes()
    assert apply(turned, [], {"f": "ABC"})[1][0]["ok"] is True
    _, report, _ = apply(turned, [], {"f": "ABCDEFGHIJKLMN"})
    assert report[0]["ok"] is False and "only 'ABCDE' would show" in report[0]["problem"]
