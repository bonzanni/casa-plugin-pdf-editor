import pymupdf
import pytest

from fixtures import cropped_form, field_form, flat_form, has_ink, image_only_page, rotated_form
from page_facts import SCALE, facts, render


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
