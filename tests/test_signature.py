import time

import pymupdf
import pytest

from fixtures import cropped_form, field_form, flat_form, landmark_signature, photo, rotated_form
from signature import SignatureStore, aspect, clean, draw_handwritten, draw_stamp


def test_clean_keeps_ink_drops_paper_and_crops():
    pix = pymupdf.Pixmap(clean(photo(1200, 900)))
    assert pix.alpha and pix.width < 700 and pix.height < 500          # cropped to the scribble
    corners = [pix.pixel(0, 0)[3], pix.pixel(pix.width - 1, pix.height - 1)[3]]
    assert max(corners) == 0                                            # paper is transparent
    opaque = [pix.pixel(x, pix.height // 2) for x in range(pix.width) if pix.pixel(x, pix.height // 2)[3] > 200]
    assert opaque and all(p[2] > p[0] for p in opaque)                  # ink kept, still blue

def test_clean_handles_a_phone_sized_photo_quickly():
    t = time.time(); out = clean(photo(4000, 3000))
    assert time.time() - t < 5 and max(pymupdf.Pixmap(out).width, pymupdf.Pixmap(out).height) <= 1200

def test_clean_refuses_a_blank_sheet():
    with pytest.raises(ValueError): clean(photo(800, 600, scribble=False))

def test_pending_is_not_stored_until_commit(tmp_path):
    s = SignatureStore(str(tmp_path)); cleaned = clean(photo(800, 600)); pid = s.set_pending(cleaned)
    assert len(pid) == 16 and s.get() is None and s.status() == {"stored": False, "pending": True, "aspect": None}
    assert s.commit(pid) is True and s.get() == cleaned
    assert s.status() == {"stored": True, "pending": False, "aspect": round(aspect(cleaned), 2)}
    assert s.commit(pid) is False and s.remove() is True and s.get() is None

def test_commit_refuses_a_preview_other_than_the_pending_one(tmp_path):
    s = SignatureStore(str(tmp_path)); first = s.set_pending(clean(photo(800, 600)))
    second = s.set_pending(clean(photo(900, 500)))
    assert first != second and s.commit(first) is False and s.get() is None
    assert s.commit(second) is True

@pytest.mark.parametrize("make", [flat_form, rotated_form, cropped_form])
def test_signature_is_where_displayed_and_upright(make):
    # landmark_signature(): 150x50 transparent PNG, black block over the left 20 px, black bar over the top 8 px
    out = draw_handwritten(make(), landmark_signature(), page=1, x=100, y=350, width=150)
    shot = pymupdf.open(stream=out, filetype="pdf")[0].get_pixmap(clip=pymupdf.Rect(100, 300, 250, 350))   # displayed convention
    dark = lambda pts: sum(1 for x, y in pts if shot.pixel(x, y)[0] < 128)
    w, h = shot.width, shot.height
    assert dark((3, y) for y in range(h)) > 0.9 * h and dark((x, 2) for x in range(w)) > 0.9 * w      # block on the left, bar on top
    assert dark((w - 4, y) for y in range(h)) < 0.3 * h and dark((x, h - 3) for x in range(w)) < 0.3 * w  # not mirrored, not turned

def test_handwritten_rests_on_the_point_and_flattens_fields():
    sig = clean(photo(800, 600))
    out = draw_handwritten(field_form(), sig, page=1, x=72, y=720, width=150)
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    rect = page.get_image_rects(page.get_images()[0][0])[0]
    assert abs(rect.x0 - 72) < 1 and abs(rect.y1 - 720) < 1 and abs(rect.width - 150) < 1
    assert abs(rect.height - 150 * aspect(sig)) < 1 and list(page.widgets()) == []

def test_stamp_carries_caption_name_and_time():
    out = draw_stamp(flat_form(), page=1, x=72, y=720, width=180, signer_name="A. Example", when="2026-11-19 14:35")
    text = pymupdf.open(stream=out, filetype="pdf")[0].get_text()
    assert "SIGNED ELECTRONICALLY" in text and text.count("A. Example") == 2 and "2026-11-19 14:35" in text

def test_off_page_signature_is_refused():
    with pytest.raises(ValueError): draw_stamp(flat_form(), page=1, x=560, y=720, width=180, signer_name="A", when="x")
