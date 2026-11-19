"""PDF fixtures generated in code. Each call builds a new PDF (fresh /ID), so a
test that compares bytes keeps the bytes it made in a variable."""
import os
import struct

import pymupdf

import casa_handoff

WIDTH, HEIGHT = 595.3, 841.9


def _new_page(doc):
    return doc.new_page(width=WIDTH, height=HEIGHT)


def _draw_flat(page):
    page.insert_text((72, 600), "Initials and surname:")
    page.draw_line((200, 602), (460, 602), dashes="[3 3] 0")
    page.insert_text((72, 640), "Signature:")
    page.draw_line((72, 720), (260, 720), dashes="[3 3] 0")
    page.insert_text((72, 750), "Date, .....-.....-2026")


def _flat_doc():
    doc = pymupdf.open()
    _draw_flat(_new_page(doc))
    return doc


def flat_form() -> bytes:
    return _flat_doc().tobytes()


def _field_doc():
    doc = pymupdf.open()
    page = _new_page(doc)
    text = pymupdf.Widget()
    text.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    text.field_name = "name"
    text.rect = pymupdf.Rect(200, 590, 400, 620)
    page.add_widget(text)
    check = pymupdf.Widget()
    check.field_type = pymupdf.PDF_WIDGET_TYPE_CHECKBOX
    check.field_name = "agree"
    check.rect = pymupdf.Rect(200, 640, 215, 655)
    page.add_widget(check)
    combo = pymupdf.Widget()
    combo.field_type = pymupdf.PDF_WIDGET_TYPE_COMBOBOX
    combo.field_name = "colour"
    combo.choice_values = ["red", "blue"]
    combo.rect = pymupdf.Rect(200, 680, 400, 700)
    page.add_widget(combo)
    return doc


def field_form() -> bytes:
    return _field_doc().tobytes()


def encrypted() -> bytes:
    return _flat_doc().tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256,
                               owner_pw="owner", user_pw="user")


def rotated_form() -> bytes:
    doc = _flat_doc()
    doc[0].set_rotation(90)
    return doc.tobytes()


def _cropped_doc():
    doc = _flat_doc()
    doc[0].set_cropbox(pymupdf.Rect(50, 100, 545, 742))
    return doc


def cropped_form() -> bytes:
    return _cropped_doc().tobytes()


def cropped_rotated_form() -> bytes:
    doc = _cropped_doc()
    doc[0].set_rotation(90)
    return doc.tobytes()


def _offset_doc():
    doc = _flat_doc()
    doc[0].set_mediabox(pymupdf.Rect(50, 0, 645.3, 841.9))
    return doc


def offset_form() -> bytes:
    return _offset_doc().tobytes()


def offset_rotated_form() -> bytes:
    doc = _offset_doc()
    doc[0].set_rotation(90)
    return doc.tobytes()


def userunit_form(rotation: int = 0) -> bytes:
    """The flat form with /UserUnit 2 (one unit is two points), optionally rotated."""
    doc = _flat_doc()
    doc.xref_set_key(doc[0].xref, "UserUnit", "2")
    doc[0].set_rotation(rotation)
    return doc.tobytes()


def signed_flag_form(flags: int) -> bytes:
    doc = _field_doc()
    cat = doc.pdf_catalog()
    kind, value = doc.xref_get_key(cat, "AcroForm")
    if kind == "xref":
        doc.xref_set_key(int(value.split()[0]), "SigFlags", str(flags))
    else:
        doc.xref_set_key(cat, "AcroForm/SigFlags", str(flags))
    return doc.tobytes()


def in_handoff(tmp_path, data, name="form.pdf") -> str:
    root = str(tmp_path / "handoff")
    os.makedirs(root, exist_ok=True)
    os.environ[casa_handoff.HANDOFF_ENV] = root
    return casa_handoff.publish("test", name, data=data)["path"]


def image_only_page() -> bytes:
    """A page whose only content is an inserted image (a scan without a text layer)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.clear_with(60)
    page.insert_image(pymupdf.Rect(72, 72, 272, 272), pixmap=pix)
    return doc.tobytes()


def has_ink(pix: pymupdf.Pixmap, inset: int = 0) -> bool:
    """Any pixel darker than mid-grey, ignoring a border of ``inset`` pixels."""
    n, stride, samples = pix.n, pix.stride, pix.samples
    colour = n - (1 if pix.alpha else 0)
    for y in range(inset, pix.height - inset):
        row = y * stride
        for x in range(inset, pix.width - inset):
            i = row + x * n
            if sum(samples[i:i + colour]) < 128 * colour:
                return True
    return False


def _dotted_doc():
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text((72, 300), "Name:")
    for i in range(80):
        x = 200 + i * 2.8
        page.draw_rect(pymupdf.Rect(x, 301, x + 1.4, 301.7), color=None, fill=(0, 0, 0))
    page.draw_rect(pymupdf.Rect(72, 400, 260, 400.6), color=None, fill=(0, 0, 0))
    return doc


def dotted_form() -> bytes:
    return _dotted_doc().tobytes()


def rotated_dotted_form() -> bytes:
    doc = _dotted_doc()
    doc[0].set_rotation(90)
    return doc.tobytes()


def rotated_field_form() -> bytes:
    doc = _field_doc()
    doc[0].set_rotation(90)
    return doc.tobytes()


def raw_span(pdf: bytes, text: str) -> dict:
    """The first span on page 1 whose text is ``text``, read with PyMuPDF directly
    (unrotated coordinates), with its ``origin`` and its line's ``dir``."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        for block in doc[0].get_text("dict")["blocks"]:
            for line in block.get("lines", ()):
                for span in line["spans"]:
                    if span["text"] == text:
                        return {**span, "origin": tuple(span["origin"]), "dir": tuple(line["dir"])}
    finally:
        doc.close()
    raise AssertionError(f"no span {text!r} on page 1")


def radio_form() -> bytes:
    """Two radio buttons grouped under one parent field 'pick', on-states 'a' and 'b'."""
    doc = pymupdf.open(); page = _new_page(doc)
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


def files_under(root: str) -> list[str]:
    """Every regular file below ``root`` (an empty leftover directory does not count)."""
    return sorted(os.path.join(d, f) for d, _, names in os.walk(root) for f in names
                  if os.path.isfile(os.path.join(d, f)))


def photo(width: int, height: int, scribble: bool = True) -> bytes:
    """A synthetic phone photo (JPEG) of ``width`` x ``height`` pixels: grey paper that
    darkens from left to right, and a blue scribble of a few thick strokes inside the
    middle third of the image. Deterministic."""
    row = bytes(v for x in range(width) for v in [215 - 45 * x // max(width - 1, 1)] * 3)
    paper = pymupdf.Pixmap(pymupdf.csRGB, width, height, row * height, False)
    doc = pymupdf.open()
    page = doc.new_page(width=width, height=height)
    page.insert_image(page.rect, pixmap=paper)
    if scribble:
        thick = min(width, height) / 60
        x0, y0 = width / 3 + 2 * thick, height / 3 + 2 * thick
        w, h = width / 3 - 4 * thick, height / 3 - 4 * thick
        strokes = [
            [(0.0, 0.7), (0.1, 0.1), (0.2, 0.8), (0.3, 0.2), (0.4, 0.75), (0.5, 0.3)],
            [(0.45, 0.6), (0.6, 0.0), (0.7, 0.9), (0.85, 0.35), (1.0, 0.5)],
            [(0.05, 1.0), (0.5, 0.85), (0.95, 0.95)],
        ]
        for stroke in strokes:
            page.draw_polyline([(x0 + u * w, y0 + v * h) for u, v in stroke],
                               color=(30 / 255, 40 / 255, 150 / 255), width=thick)
    return page.get_pixmap().tobytes("jpg", jpg_quality=90)


def landmark_signature() -> bytes:
    """A 150 x 50 PNG with alpha, transparent except an opaque black block over the
    left 20 px and an opaque black bar over the top 8 px: shows position and turn."""
    width, height = 150, 50
    samples = bytearray(width * height * 4)
    for y in range(height):
        for x in range(width):
            if x < 20 or y < 8:
                samples[(y * width + x) * 4 + 3] = 255
    return pymupdf.Pixmap(pymupdf.csRGB, width, height, bytes(samples), True).tobytes("png")


def white_field_form() -> bytes:
    """A text field with an opaque white background at (140, 680, 340, 730)."""
    doc = pymupdf.open()
    page = _new_page(doc)
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "sign_here"
    w.fill_color = (1, 1, 1)
    w.rect = pymupdf.Rect(140, 680, 340, 730)
    page.add_widget(w)
    return doc.tobytes()


def sideways(jpg: bytes) -> bytes:
    """The JPEG ``jpg`` with its pixels turned 90 degrees counter-clockwise, same resolution."""
    doc = pymupdf.open(stream=jpg, filetype="jpg")
    page = doc[0]
    scale = page.get_image_info()[0]["width"] / page.rect.width
    return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale).prerotate(270)).tobytes("jpg", jpg_quality=90)


def with_orientation(jpg: bytes, orientation: int) -> bytes:
    """``jpg`` with an Exif APP1 segment carrying only the Orientation tag."""
    tiff = (b"MM\x00*\x00\x00\x00\x08" + struct.pack(">H", 1)
            + struct.pack(">HHIHH", 0x0112, 3, 1, orientation, 0) + b"\x00\x00\x00\x00")
    body = b"Exif\x00\x00" + tiff
    return jpg[:2] + b"\xff\xe1" + struct.pack(">H", len(body) + 2) + body + jpg[2:]


def ink_in(pdf: bytes, page: int, rect, below: int = 128) -> int:
    """Dark pixels (grey under ``below``) in the displayed rectangle ``rect`` of ``page``
    (1-based), rendered at 2 pixels per point."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    try:
        pix = doc[page - 1].get_pixmap(matrix=pymupdf.Matrix(2, 2), clip=pymupdf.Rect(rect), alpha=False,
                                       colorspace=pymupdf.csGRAY)
        return sum(1 for v in pix.samples if v < below)
    finally:
        doc.close()


def fonts_premapped(names: tuple[str, ...] = ("dvs",)) -> bytes:
    """A blank page whose resources already map each of ``names`` to Courier, as a PDF made
    elsewhere may do."""
    doc = pymupdf.open()
    page = _new_page(doc)
    xref = doc.get_new_xref()
    doc.update_object(xref, "<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")
    fonts = " ".join(f"/{n} {xref} 0 R" for n in names)
    doc.xref_set_key(page.xref, "Resources", f"<< /Font << {fonts} >> >>")
    return doc.tobytes()


def blank_page() -> bytes:
    doc = pymupdf.open()
    _new_page(doc)
    return doc.tobytes()


def spoofed_dejavu() -> bytes:
    """A blank page with Caveat embedded as /dvs, its font names rewritten to DejaVu Sans's."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_font(fontname="dvs", fontfile=os.path.join(os.path.dirname(__file__), "..", "server",
                                                           "fonts", "Caveat-Regular.ttf"))
    for xref, *_ in page.get_fonts(full=True):
        doc.xref_set_key(xref, "BaseFont", "/DejaVu#20Sans#20Book")
        kind, kids = doc.xref_get_key(xref, "DescendantFonts")
        if kind == "array":
            doc.xref_set_key(int(kids.strip("[]").split()[0]), "BaseFont", "/DejaVu#20Sans#20Book")
    return doc.tobytes()


def remapped_dejavu() -> bytes:
    """A blank page with the bundled DejaVu Sans embedded as /dvs, byte for byte, but with a
    CIDToGIDMap that draws CID i with glyph i + 1, as another producer may legitimately encode it."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_font(fontname="dvs", fontfile=os.path.join(os.path.dirname(__file__), "..", "server",
                                                           "fonts", "DejaVuSans.ttf"))
    xref = page.get_fonts(full=True)[0][0]
    descendant = int(doc.xref_get_key(xref, "DescendantFonts")[1].strip("[]").split()[0])
    table = doc.get_new_xref()
    doc.update_object(table, "<<>>")
    doc.update_stream(table, b"".join((i + 1).to_bytes(2, "big") for i in range(0xFFFF)))
    doc.xref_set_key(descendant, "CIDToGIDMap", f"{table} 0 R")
    return doc.tobytes()


def caveat_form() -> bytes:
    """Printed text in an embedded Caveat, and a text field `f` whose /DA names that font
    through the form's /DR, as a form made elsewhere may carry its own field font."""
    doc = pymupdf.open()
    page = _new_page(doc)
    page.insert_text((72, 72), "Printed in Caveat", fontname="cav", fontsize=14,
                     fontfile=os.path.join(os.path.dirname(__file__), "..", "server", "fonts", "Caveat-Regular.ttf"))
    w = pymupdf.Widget()
    w.field_type = pymupdf.PDF_WIDGET_TYPE_TEXT
    w.field_name = "f"
    w.rect = pymupdf.Rect(72, 100, 372, 130)
    page.add_widget(w)
    doc = pymupdf.open(stream=doc.tobytes(), filetype="pdf")
    cav = next(f[0] for f in doc[0].get_fonts(full=True) if f[4] == "cav")
    doc.xref_set_key(doc.pdf_catalog(), "AcroForm/DR", f"<</Font<</cav {cav} 0 R>>>>")
    doc.xref_set_key(next(doc[0].widgets()).xref, "DA", "(/cav 12 Tf 0 g)")
    return doc.tobytes()


def embedded_fonts(pdf: bytes) -> dict[str, bytes]:
    """Every embedded font program in ``pdf`` by base font name, read from the font objects
    themselves (xref numbers change when a document is saved with garbage collection)."""
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    out = {}
    for xref in range(1, doc.xref_length()):
        if doc.xref_get_key(xref, "Type")[1] == "/Font" and doc.xref_get_key(xref, "Subtype")[1] != "/CIDFontType2":
            name, ext, _, buffer = doc.extract_font(xref)
            if buffer:
                out[f"{name}:{xref}" if name in out else name] = buffer
    return out
