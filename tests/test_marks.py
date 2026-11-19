import pymupdf

from fixtures import cropped_form, field_form, flat_form, has_ink, raw_span, rotated_field_form, rotated_form
from marks import apply
from page_facts import facts, to_page_rect


def test_text_lands_at_the_baseline_asked_for():
    out, report, pages = apply(flat_form(), [{"kind": "text", "page": 1, "x": 205, "y": 600, "text": "A. Example"}], {})
    t = next(t for t in facts(out, 1)["text"] if t["text"] == "A. Example")
    assert abs(t["x0"] - 205) < 1.5 and abs(t["baseline"] - 600) < 1.5
    assert report == [{"ok": True, "what": "text 'A. Example' on page 1", "problem": None}] and pages == [1]

def test_text_outside_latin1_renders_as_glyphs():
    out, _, _ = apply(flat_form(), [{"kind": "text", "page": 1, "x": 205, "y": 600, "text": "Łukasz Ćwik"}], {})
    assert any(t["text"] == "Łukasz Ćwik" for t in facts(out, 1)["text"])

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

def test_mark_on_a_rotated_page_lands_where_displayed():
    out, _, _ = apply(rotated_form(), [{"kind": "text", "page": 1, "x": 100, "y": 100, "text": "HERE"}], {})
    span = raw_span(out, "HERE")                 # read with pymupdf directly, in unrotated coordinates
    # displayed (100, 100) on a page 841.9 high rotated 90 is unrotated (100, 741.9); upright there means reading direction (0, -1)
    assert abs(span["origin"][0] - 100) < 1.5 and abs(span["origin"][1] - 741.9) < 1.5 and span["dir"] == (0.0, -1.0)

def test_mark_on_a_cropped_page_lands_where_displayed():
    out, _, _ = apply(cropped_form(), [{"kind": "text", "page": 1, "x": 100, "y": 100, "text": "HERE"}], {})
    doc = pymupdf.open(stream=out, filetype="pdf"); doc[0].set_cropbox(doc[0].mediabox)
    span = raw_span(doc.tobytes(), "HERE")       # with the crop lifted, the mark is 50 right and 100 down
    assert abs(span["origin"][0] - 150) < 1.5 and abs(span["origin"][1] - 200) < 1.5

def test_fields_are_set_and_read_back():
    out, report, _ = apply(field_form(), [], {"name": "A. Example", "agree": True, "colour": "blue", "nope": 1, "colour2": "x"})
    values = {f["name"]: f["value"] for f in facts(out, 1)["fields"]}
    assert values == {"name": "A. Example", "agree": True, "colour": "blue"}
    assert [r["ok"] for r in report] == [True, True, True, False, False]

def test_field_on_a_rotated_page_is_reported_and_filled_where_displayed():
    # field_form's text field `name` is at Rect(200, 590, 400, 620); rotated 90 on an 841.9 pt high page that displays at (221.9, 200, 251.9, 400)
    f = next(x for x in facts(rotated_field_form(), 1)["fields"] if x["name"] == "name")
    assert all(abs(a - b) < 1 for a, b in zip((f["x0"], f["y0"], f["x1"], f["y1"]), (221.9, 200, 251.9, 400)))
    out, _, _ = apply(rotated_field_form(), [], {"name": "A. Example"})
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
    doc = pymupdf.open(stream=out, filetype="pdf"); page = doc[0]
    from page_facts import to_displayed
    rects = [to_displayed(page, d["rect"]) for d in page.get_drawings() if d.get("color") == (0.5, 0.5, 0.5)]
    assert any(abs(r.x0 - 100) < 1 and abs(r.y0 - 400) < 1 and abs(r.x1 - 250) < 1 and abs(r.y1 - 440) < 1 for r in rects)

def test_tick_and_cross_draw_ink_inside_their_square():
    # displayed y 400 on rotated_form is blank across x 80..220 (its content sits at displayed y < 270)
    out, report, _ = apply(rotated_form(), [{"kind": "tick", "page": 1, "x": 100, "y": 400, "size": 20},
                                            {"kind": "cross", "page": 1, "x": 200, "y": 400, "size": 20}], {})
    assert all(r["ok"] for r in report)
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    for cx in (100, 200):
        assert has_ink(page.get_pixmap(clip=pymupdf.Rect(cx - 10, 390, cx + 10, 410)))
        around = [pymupdf.Rect(cx - 16, 384, cx + 16, 389), pymupdf.Rect(cx - 16, 411, cx + 16, 416),
                  pymupdf.Rect(cx - 16, 384, cx - 11, 416), pymupdf.Rect(cx + 11, 384, cx + 16, 416)]
        assert not any(has_ink(page.get_pixmap(clip=r)) for r in around)

def test_field_values_of_the_wrong_kind_are_refused():
    _, report, _ = apply(field_form(), [], {"name": 5, "agree": "yes", "colour": "green"})
    assert [r["ok"] for r in report] == [False, False, False]
    assert "string" in report[0]["problem"] and "true or false" in report[1]["problem"]
    assert "red, blue" in report[2]["problem"]


def _radio_form() -> bytes:
    """Two radio buttons grouped under one parent field 'pick', on-states 'a' and 'b'."""
    doc = pymupdf.open(); page = doc.new_page()
    for r in [(100, 100, 115, 115), (100, 130, 115, 145)]:
        w = pymupdf.Widget(); w.field_type = pymupdf.PDF_WIDGET_TYPE_RADIOBUTTON; w.field_name = "pick"
        w.rect = pymupdf.Rect(r); w.field_value = False
        page.add_widget(w)
    doc = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
    kids = [w.xref for w in doc[0].widgets()]
    parent = doc.get_new_xref()
    doc.update_object(parent, f"<< /FT /Btn /Ff 49152 /T (pick) /V /Off /Kids [{' '.join(f'{k} 0 R' for k in kids)}] >>")
    for k, state in zip(kids, "ab"):
        off, on = doc.xref_get_key(k, "AP/N/Off")[1], doc.xref_get_key(k, "AP/N/Yes")[1]
        for key in ("T", "FT", "Ff", "V"):
            doc.xref_set_key(k, key, "null")
        doc.xref_set_key(k, "Parent", f"{parent} 0 R")
        doc.xref_set_key(k, "AP", f"<< /N << /Off {off} /{state} {on} >> >>")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/Fields", f"[{parent} 0 R]")
    return doc.tobytes()

def test_radio_group_takes_the_on_state_of_one_button():
    out, report, _ = apply(_radio_form(), [], {"pick": "b"})
    assert report == [{"ok": True, "what": "field 'pick'", "problem": None}]
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
