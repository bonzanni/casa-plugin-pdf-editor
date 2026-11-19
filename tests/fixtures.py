"""PDF fixtures generated in code. Each call builds a new PDF (fresh /ID), so a
test that compares bytes keeps the bytes it made in a variable."""
import os

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
