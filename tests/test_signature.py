import os
import time

import pymupdf
import pytest

from fixtures import (cropped_form, field_form, flat_form, landmark_signature, photo, rotated_form, sideways,
                      white_field_form, with_orientation)
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

def test_stamp_name_is_dark_blue_ink():
    out = draw_stamp(flat_form(), page=1, x=72, y=720, width=180, signer_name="A. Example", when="x")
    spans = [s for b in pymupdf.open(stream=out, filetype="pdf")[0].get_text("dict")["blocks"] for l in b.get("lines", ()) for s in l["spans"]]
    big = max((s for s in spans if s["text"] == "A. Example"), key=lambda s: s["size"])
    assert all(abs(a - b) <= 2 for a, b in zip(pymupdf.sRGB_to_rgb(big["color"]), (26, 41, 107)))

def test_off_page_signature_is_refused():
    with pytest.raises(ValueError): draw_stamp(flat_form(), page=1, x=560, y=720, width=180, signer_name="A", when="x")


def _ink_grid(png, cols=24, rows=18):
    """Which cells of a coarse grid over the image hold ink (alpha over 64)."""
    pix = pymupdf.Pixmap(pymupdf.Pixmap(png), cols, rows)
    return [pix.pixel(x, y)[3] > 64 for y in range(rows) for x in range(cols)]

def _same_signature(a, b):
    pa, pb = pymupdf.Pixmap(a), pymupdf.Pixmap(b)
    assert abs(pa.width - pb.width) <= 4 and abs(pa.height - pb.height) <= 4
    ga, gb = _ink_grid(a), _ink_grid(b)
    assert sum(x == y for x, y in zip(ga, gb)) >= 0.95 * len(ga)

def test_clean_follows_the_exif_orientation():
    p = photo(1200, 900)
    _same_signature(clean(with_orientation(sideways(p), 6)), clean(p))

def test_clean_turns_a_sideways_photo_on_request():
    p = photo(1200, 900)
    a, b = pymupdf.Pixmap(clean(p)), pymupdf.Pixmap(clean(p, rotate=90))
    assert abs(a.width - b.height) <= 4 and abs(a.height - b.width) <= 4
    _same_signature(clean(sideways(p), rotate=90), clean(p))
    with pytest.raises(ValueError): clean(p, rotate=45)

def test_clean_takes_png_and_refuses_too_large_or_unknown_input():
    png = pymupdf.Pixmap(photo(800, 600)).tobytes("png")
    assert pymupdf.Pixmap(clean(png)).alpha
    for bad in (b"not an image", flat_form(), b"\xff\xd8" + b"\x00" * (30 * 2**20)):
        with pytest.raises(ValueError): clean(bad)

def test_commit_stores_the_approved_bytes_even_if_a_new_preview_lands(tmp_path, monkeypatch):
    s = SignatureStore(str(tmp_path)); approved = clean(photo(800, 600)); pid = s.set_pending(approved)
    newer = clean(photo(900, 500)); real_read = SignatureStore._read; calls = []
    def read(path):
        data = real_read(path)
        if path.endswith("pending.png") and not calls:
            calls.append(1); s.set_pending(newer)     # lands between the check and the store
        return data
    monkeypatch.setattr(s, "_read", read)
    assert s.commit(pid) is True
    monkeypatch.undo()
    assert s.get() == approved and s.status()["pending"] is True
    assert s.commit(SignatureStore(str(tmp_path)).set_pending(newer)) is True and s.get() == newer

def test_store_tightens_an_existing_directory(tmp_path):
    (tmp_path / "signature").mkdir(mode=0o755); (tmp_path / "signature").chmod(0o755)
    SignatureStore(str(tmp_path)).set_pending(clean(photo(800, 600)))
    assert (tmp_path / "signature").stat().st_mode & 0o777 == 0o700
    assert (tmp_path / "signature" / "pending.png").stat().st_mode & 0o777 == 0o600

def _inked(pix):
    return sum(1 for y in range(pix.height) for x in range(pix.width) if min(pix.pixel(x, y)[:3]) < 160)

def test_both_looks_show_over_a_white_form_field():
    out = draw_handwritten(white_field_form(), landmark_signature(), page=1, x=150, y=725, width=150)
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    assert list(page.widgets()) == []
    assert _inked(page.get_pixmap(clip=pymupdf.Rect(150, 685, 300, 725))) > 600    # the 20 pt block alone is ~800 px
    out = draw_stamp(white_field_form(), page=1, x=150, y=725, width=150, signer_name="A. Example", when="2026-11-19 14:35")
    shot = pymupdf.open(stream=out, filetype="pdf")[0].get_pixmap(clip=pymupdf.Rect(150, 690, 300, 725))
    assert _inked(shot) > 200                                                        # name and small print inside the field

def test_erase_wipes_the_whole_signature_directory(tmp_path):
    s = SignatureStore(str(tmp_path)); s.commit(s.set_pending(clean(photo(800, 600))))
    s.set_pending(clean(photo(900, 500)))
    (tmp_path / "signature" / ".write-x.tmp").write_bytes(b"signature bytes")
    assert s.erase() == (True, []) and os.listdir(tmp_path / "signature") == []
    assert s.erase() == (False, []) and SignatureStore(str(tmp_path / "none")).erase() == (False, [])

def test_erase_reports_a_file_it_could_not_remove(tmp_path, monkeypatch):
    s = SignatureStore(str(tmp_path)); s.commit(s.set_pending(clean(photo(800, 600))))
    real = os.unlink
    monkeypatch.setattr(os, "unlink", lambda p, *a, **k: (_ for _ in ()).throw(PermissionError(13, "Permission denied", p))
                        if str(p).endswith("signature.png") else real(p, *a, **k))
    assert s.erase() == (False, ["signature.png"]) and s.get() is not None


def test_stamp_is_unchanged_by_page_fonts_named_like_its_own():
    from fixtures import blank_page, fonts_premapped, ink_in
    args = dict(page=1, x=72, y=720, width=180, signer_name="A. Example", when="2026-11-19 14:35")
    out = draw_stamp(fonts_premapped(("dvs", "sig")), **args)
    control = draw_stamp(blank_page(), **args)
    box = (72, 660, 252, 720)
    assert ink_in(control, 1, box) > 100 and ink_in(out, 1, box) == ink_in(control, 1, box)
    shown = [pymupdf.open(stream=p, filetype="pdf")[0].get_text() for p in (out, control)]
    assert shown[0] == shown[1] and "A. Example" in shown[0]
