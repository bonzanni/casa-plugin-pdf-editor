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
