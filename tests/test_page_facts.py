import pymupdf
import pytest

from fixtures import (_flat_doc, cropped_form, dotted_form, field_form, flat_form, has_ink, image_only_page,
                      rotated_dotted_form, rotated_form)
from page_facts import SCALE, facts, render, to_displayed


def test_facts_report_text_lines_and_drawn_lines():
    f = facts(flat_form(), 1)
    label = next(t for t in f["text"] if t["text"].startswith("Initials"))
    assert abs(label["x0"] - 72) < 1 and abs(label["baseline"] - 600) < 1
    assert any(abs(l["x0"] - 200) < 1 and abs(l["x1"] - 460) < 1 and abs(l["y0"] - 602) < 1 for l in f["lines"])
    assert (f["width"], f["height"], f["pages"]) == (595.3, 841.9, 1)

def test_facts_list_form_fields():
    f = facts(field_form(), 1)
    assert {x["name"]: x["type"] for x in f["fields"]} == {"name": "text", "agree": "checkbox", "colour": "combobox"}
    assert next(x for x in f["fields"] if x["name"] == "colour")["options"] == ["red", "blue"]

def test_rotated_page_facts_are_in_displayed_coordinates():
    f = facts(rotated_form(), 1)
    assert (f["width"], f["height"]) == (841.9, 595.3)
    assert all(0 <= t["x0"] <= f["width"] and 0 <= t["y0"] <= f["height"] for t in f["text"])

def test_rotated_page_positions_match_hand_worked_values():
    # flat_form puts "Signature:" at baseline (72, 640) and a line (72,720)-(260,720); rotated 90 on an 841.9 pt high page,
    # a point (x, y) is displayed at (841.9 - y, x)
    f = facts(rotated_form(), 1)
    label = next(t for t in f["text"] if t["text"].startswith("Signature"))
    assert abs(label["x0"] - 198.6) < 1 and abs(label["y0"] - 72.0) < 1 and label["y1"] - label["y0"] > label["x1"] - label["x0"]
    assert any(abs(l["x0"] - 121.9) < 1 and abs(l["y0"] - 72) < 1 and abs(l["y1"] - 260) < 1 for l in f["lines"])

def test_cropped_page_positions_are_relative_to_the_visible_area():
    f = facts(cropped_form(), 1)
    label = next(t for t in f["text"] if t["text"].startswith("Signature"))
    assert (f["width"], f["height"]) == (495.0, 642.0) and abs(label["x0"] - 22) < 1 and abs(label["baseline"] - 540) < 1

def test_region_on_a_rotated_page_shows_the_displayed_area():
    crop = pymupdf.Pixmap(render(rotated_form(), 1, region=(190, 65, 220, 130)))     # around the rotated "Signature:" label
    assert abs(crop.width - 30 * SCALE * 2) <= 2 and abs(crop.height - 65 * SCALE * 2) <= 2
    assert has_ink(crop, inset=20) and not has_ink(pymupdf.Pixmap(render(rotated_form(), 1, region=(400, 300, 430, 365))), inset=20)

def test_render_is_png_at_scale_and_region_zooms():
    png = render(flat_form(), 1); pix = pymupdf.Pixmap(png)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and abs(pix.width - 595.3 * SCALE) <= 2
    zoom = pymupdf.Pixmap(render(flat_form(), 1, region=(60, 580, 480, 760)))
    assert abs(zoom.width - 420 * SCALE * 2) <= 2

def test_scan_only_page_says_so():
    assert "no text layer" in facts(image_only_page(), 1)["note"]

def test_page_out_of_range_names_the_valid_range():
    with pytest.raises(ValueError, match="1 to 1"):
        facts(flat_form(), 2)
    with pytest.raises(ValueError, match="1 to 1"):
        render(flat_form(), 0)


@pytest.mark.parametrize("make, size", [(flat_form, "595.3 x 841.9"), (rotated_form, "841.9 x 595.3")])
@pytest.mark.parametrize("region", [(700, 900, 800, 1000), (100, 100, 100, 200), (500, 800, 900, 900)])
def test_off_page_or_degenerate_region_is_a_plain_refusal(make, size, region):
    if make is flat_form and region == (500, 800, 900, 900):
        region = (500, 900, 900, 1000)      # (500, 800, ...) overlaps the upright page
    with pytest.raises(ValueError, match=size):
        render(make(), 1, region=region)

def test_dotted_run_and_thin_bar_are_lines():
    f = facts(dotted_form(), 1)
    assert len(f["lines"]) == 2 and f["boxes"] == []
    assert any(abs(l["x0"] - 200) < 0.5 and abs(l["x1"] - 422.6) < 0.5
               and abs(l["y0"] - 301.4) < 0.5 and abs(l["y1"] - 301.4) < 0.5 for l in f["lines"])
    assert any(abs(l["x0"] - 72) < 0.5 and abs(l["x1"] - 260) < 0.5
               and abs(l["y0"] - 400.3) < 0.5 and abs(l["y1"] - 400.3) < 0.5 for l in f["lines"])

def test_dotted_run_on_a_rotated_page_is_vertical_as_displayed():
    f = facts(rotated_dotted_form(), 1)
    assert any(abs(l["x0"] - 540.6) < 0.5 and abs(l["x1"] - 540.6) < 0.5
               and abs(l["y0"] - 200) < 0.5 and abs(l["y1"] - 422.6) < 0.5 for l in f["lines"])

def test_content_hidden_by_the_cropbox_is_not_reported():
    doc = _flat_doc(); doc[0].set_cropbox(pymupdf.Rect(50, 100, 545, 700))    # hides the signature line and the date
    f = facts(doc.tobytes(), 1)
    items = f["text"] + f["lines"] + f["boxes"] + f["fields"]
    assert items and all(i["y0"] <= f["height"] and i["x0"] <= f["width"] for i in items)
    assert not any(t["text"].startswith("Date") for t in f["text"]) and len(f["lines"]) == 1
    g = facts(cropped_form(), 1)
    assert all(i["y0"] <= g["height"] for i in g["text"] + g["lines"])

def test_to_displayed_takes_a_pair_as_a_point():
    page = pymupdf.open(stream=rotated_form(), filetype="pdf")[0]
    assert to_displayed(page, (10, 20)) == to_displayed(page, pymupdf.Point(10, 20))


def test_radio_buttons_list_their_group_choices_and_own_rectangles():
    from fixtures import radio_form
    buttons = [f for f in facts(radio_form(), 1)["fields"] if f["name"] == "pick"]
    assert [b["type"] for b in buttons] == ["radiobutton", "radiobutton"]
    assert [b["options"] for b in buttons] == [["a", "b"], ["a", "b"]]
    assert [(b["x0"], b["y0"], b["x1"], b["y1"]) for b in buttons] == [(100, 100, 115, 115), (100, 130, 115, 145)]


def test_font_alias_never_reuses_a_font_in_the_document():
    import os
    from fixtures import blank_page, fonts_premapped
    from page_facts import font_alias
    dejavu = os.path.join(os.path.dirname(__file__), "..", "server", "fonts", "DejaVuSans.ttf")
    taken = pymupdf.open(stream=fonts_premapped(("dvs", "dvs1")), filetype="pdf")[0]
    assert font_alias(taken, "dvs") == "dvs2"
    free = pymupdf.open(stream=blank_page(), filetype="pdf")[0]
    assert font_alias(free, "dvs") == "dvs"
    free.insert_text((72, 72), "x", fontname="dvs", fontfile=dejavu)
    assert font_alias(free, "dvs") == "dvs1"   # even the plugin's own font from an earlier write
