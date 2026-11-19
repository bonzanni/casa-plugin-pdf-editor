"""The tool surface: what the assistant calls, and the only place the modules meet.

Every tool returns JSON text (``_ok``); a tool that shows a page returns one
image per page shown before it. A refusal from a module (``Refused``,
``DeliveryFailed``, ``ValueError``) is raised as a ``ValueError`` whose message
is the reason sentence; anything else propagates. The stores are built from
``CLAUDE_PLUGIN_DATA`` at each call, so nothing is held between calls.

``send_pdf_to_chat`` and ``preview_signature`` are capability tools in Casa's
result contract: their result is JSON only (Casa's parser rejects images), with
the broker reference under ``file``; Casa replaces it with a receipt.
"""
from __future__ import annotations

import functools
import json
import os
import time

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.utilities.types import Image

import casa_handoff
import delivery
import marks as marks_mod
import page_facts
import signature
from documents import Refused, Store

DEFAULT_DATA = "/tmp/pdf-editor-plugin-data"
PREVIEW_NAME = "signature-preview.png"
PREVIEW_CAPTION = "Signature preview: is it upright and clean?"
LOOKS = ("handwritten", "stamp")

mcp = FastMCP("pdf-editor")


def _data_root() -> str:
    return os.environ.get("CLAUDE_PLUGIN_DATA") or DEFAULT_DATA


def _docs() -> Store:
    return Store(_data_root())


def _signatures() -> signature.SignatureStore:
    return signature.SignatureStore(_data_root())


def _ok(data: dict) -> str:
    return json.dumps(data, ensure_ascii=False)


def _image(png: bytes) -> Image:
    return Image(data=png, format="png")


def _plain(fn):
    """Turn a module's refusal into a ValueError carrying only its reason sentence."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (Refused, delivery.DeliveryFailed) as exc:
            raise ValueError(exc.reason) from None
    return wrapper


@mcp.tool()
@_plain
def open_pdf(path: str) -> list:
    """Open a PDF the operator sent (its handoff path) to fill in or sign. Returns doc_id, the
    page count, the current version, page 1's image (with a ruler in PDF points) and page 1's
    facts: text, lines, boxes and form fields with their positions. Coordinates everywhere are
    PDF points from the top-left of the page as displayed, x right, y down. Next: look at the
    other pages with look_at_page, then write_on_pdf. A 'warning' means the file carries a
    digital signature that any change invalidates; tell the operator."""
    docs = _docs()
    doc = docs.open(path)
    data = docs.latest_bytes(doc.id)
    info = {"doc_id": doc.id, "filename": doc.filename, "pages": doc.pages,
            "version": doc.version, "signed": doc.signed}
    if doc.has_digital_signature:
        info["warning"] = ("This PDF carries a digital signature; any change made here "
                           "invalidates it.")
    return [_image(page_facts.render(data, 1)),
            _ok({**info, "facts": page_facts.facts(data, 1)})]


@mcp.tool()
@_plain
def look_at_page(doc_id: str, page: int = 1, region: list[float] | None = None) -> list:
    """Look at one page (1-based) of the current version: its image with a ruler in PDF points,
    and its facts (text, lines, boxes, form fields, with positions in points from the top-left
    of the page as displayed). region [x0, y0, x1, y1] in points zooms the image in on that
    area at double resolution; the facts are always the whole page's. Use it to find where to
    write and to check marks closely."""
    docs = _docs()
    doc = docs.get(doc_id)
    data = docs.latest_bytes(doc_id)
    if region is not None and len(region) != 4:
        raise ValueError("region must be four numbers [x0, y0, x1, y1] in points.")
    png = page_facts.render(data, page, region=None if region is None else tuple(region))
    return [_image(png), _ok({"version": doc.version, **page_facts.facts(data, page)})]


@mcp.tool()
@_plain
def write_on_pdf(doc_id: str, marks: list[dict] = [], fields: dict = {},
                 preview_only: bool = False) -> list:
    """Write on the document, making a new version. marks, each with page and points
    from the top-left as displayed: text {x, y (baseline start), text, size?, max_width?}; tick
    and cross {x, y (centre), size?}; strike {x0, y0, x1, y1}; placeholder {x, y (top-left),
    width, height}, a grey box allowed only with preview_only. fields: {name: value} (string,
    true/false for checkboxes, an option for radio and choice). Returns a report per item and
    images of the pages touched. Look at the returned image and correct anything misplaced
    before showing the operator; a mark over a form field may be hidden by the field (the
    image shows the truth). preview_only draws without storing. Writing on a signed document
    starts from its last unsigned version."""
    docs = _docs()
    doc = docs.get(doc_id)
    if not preview_only and any(isinstance(m, dict) and m.get("kind") == "placeholder"
                                for m in (marks if isinstance(marks, list) else [marks])):
        raise ValueError("A placeholder mark is only for trying a position: call write_on_pdf "
                         "with preview_only true, or leave the placeholder out.")
    base = docs.latest_unsigned_bytes(doc_id) if doc.signed else docs.latest_bytes(doc_id)
    new, report, touched = marks_mod.apply(base, marks, fields)
    out = {"report": report, "version": doc.version, "preview_only": preview_only}
    if not touched:
        return [_ok({**out, "note": "Nothing was applied; the document is unchanged."})]
    if not preview_only:
        out["version"] = docs.add_version(doc_id, new)
        if doc.signed:
            out["signature_removed"] = True
    out["pages_shown"] = touched
    return [*(_image(page_facts.render(new, p)) for p in touched), _ok(out)]


@mcp.tool()
@_plain
def undo_last_write(doc_id: str) -> list:
    """Drop the current version and go back to the one before (a signing is undone too).
    Returns the version now current and page 1's image. The dropped version's number is never
    reused."""
    docs = _docs()
    docs.undo(doc_id)
    doc = docs.get(doc_id)
    return [_image(page_facts.render(docs.latest_bytes(doc_id), 1)),
            _ok({"version": doc.version, "signed": doc.signed})]


@mcp.tool()
@_plain
def send_pdf_to_chat(doc_id: str, caption: str = "") -> str:
    """Send the current version to the operator's chat, named '<file> (draft).pdf' or
    '<file> (signed).pdf', with an optional one-line caption. Use it to show a draft before
    asking to sign, and to deliver the signed result. Also returns handoff_path, the same PDF
    for another plugin (for example to attach to an email)."""
    docs = _docs()
    doc = docs.get(doc_id)
    return _ok(delivery.send_pdf(docs.latest_bytes(doc_id), doc.filename,
                                 signed=doc.signed, caption=caption))


@mcp.tool()
@_plain
def sign_pdf(doc_id: str, filename: str, version: int, look: str, page: int,
             x: float, y: float, width: float, signer_name: str = "") -> list:
    """Sign the current version after the operator approved this exact draft; needs their
    approval. filename and version must be the document's current ones (as last shown).
    look 'handwritten' places the stored signature; 'stamp' draws a framed electronic-signature
    stamp with signer_name and the time. (x, y) is the bottom-left corner in points from the
    top-left of the page as displayed, width in points (height follows: the signature's aspect,
    or width / 3 for the stamp). Then check the returned image and send_pdf_to_chat."""
    docs = _docs()
    doc = docs.get(doc_id)
    if filename != doc.filename:
        raise ValueError(f"The document is {doc.filename!r}, not {filename!r}; nothing was signed.")
    if version != doc.version:
        raise ValueError(f"the document changed since version {version}; show the current draft "
                         "and ask again")
    if doc.signed:
        raise ValueError("already signed; write on it again to start a new unsigned version")
    if look not in LOOKS:
        raise ValueError(f"look must be 'handwritten' or 'stamp', not {look!r}.")
    data = docs.latest_bytes(doc_id)
    if look == "handwritten":
        sig = _signatures().get()
        if sig is None:
            raise ValueError("no signature is stored; use look 'stamp' or register one")
        signed = signature.draw_handwritten(data, sig, page=page, x=x, y=y, width=width)
    else:
        if not signer_name.strip():
            raise ValueError("look 'stamp' needs signer_name, the name written in the stamp.")
        signed = signature.draw_stamp(data, page=page, x=x, y=y, width=width,
                                      signer_name=signer_name,
                                      when=time.strftime("%Y-%m-%d %H:%M"))
    new_version = docs.add_version(doc_id, signed, signed=True)
    return [_image(page_facts.render(signed, page)),
            _ok({"version": new_version, "signed": True, "page": page})]


@mcp.tool()
@_plain
def preview_signature(path: str, rotate: int = 0) -> str:
    """Register a signature, step 1: clean the photo of a handwritten signature (its handoff
    path) and show the cleaned result to the operator in chat. You do not see the preview; the
    operator does. If they say it is sideways or upside down, call again with rotate 90, 180 or
    270 (degrees clockwise). When they are happy, call store_signature with the returned
    preview_id."""
    try:
        _, photo = casa_handoff.capture(path)
    except casa_handoff.HandoffError as exc:
        raise ValueError(f"That path is not a file in the handoff folder ({exc}).") from None
    cleaned = signature.clean(photo, rotate=rotate)
    sent = delivery.send_image(signature.preview(cleaned), PREVIEW_NAME, PREVIEW_CAPTION)
    return _ok({**sent, "preview_id": _signatures().set_pending(cleaned)})


@mcp.tool()
@_plain
def store_signature(preview_id: str) -> str:
    """Register a signature, step 2: store the signature last previewed with preview_signature
    as the one used for signing, replacing any earlier one; needs the operator's approval.
    preview_id must be the one preview_signature returned last."""
    if not _signatures().commit(preview_id):
        raise ValueError("that preview is no longer the pending one; preview again")
    return _ok(_signatures().status())


@mcp.tool()
def signature_status() -> str:
    """Whether a signature is stored (and its height / width as 'aspect', to size it), and
    whether a preview is pending. Use it before choosing sign_pdf's look."""
    return _ok(_signatures().status())


@mcp.tool()
def remove_signature() -> str:
    """Delete the stored signature and any pending preview; needs the operator's approval."""
    sigs = _signatures()
    sigs.remove()
    return _ok(sigs.status())


@mcp.tool()
def erase_pdf_editor() -> str:
    """Delete every working document and the stored signature (and any pending preview)."""
    removed = _docs().erase_documents()
    return _ok({"documents_removed": removed, "signature_removed": _signatures().remove()})


@mcp.tool()
def erase_pdf_documents() -> str:
    """Delete every working document; the stored signature is kept."""
    return _ok({"documents_removed": _docs().erase_documents()})


if __name__ == "__main__":
    mcp.run()
