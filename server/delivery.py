"""Showing a file in the operator's chat, and passing a PDF on through the
handoff folder.

Casa sends only files staged in its plugin outbox, deposited with its broker as
an `operator_file`; Casa posts the file after the tool returns. `send_pdf`
publishes the PDF to the handoff folder (so other plugins can take it by path),
stages the same bytes in the outbox and deposits them, in that order. The
deposit is last because it cannot be taken back: once it succeeds Casa will
send the file. A failure at staging or deposit removes the staged file and the
published handoff file, so a failed call leaves nothing behind.
"""
from __future__ import annotations

import contextlib
import os
import secrets
import tempfile

import casa_broker
import casa_handoff

FILE_SLOT = "file"
PRODUCER = "pdf-editor"
OUTBOX_ENV = "CASA_PLUGIN_OUTBOX_DIR"
DEFAULT_OUTBOX = "/data/plugin-outbox"
CHAT_MAX_BYTES = 20 * 1024 * 1024
_deposit_file = casa_broker.deposit_file     # test seam


class DeliveryFailed(Exception):
    """Nothing reached the chat. `reason` is a sentence for the assistant to relay."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def chat_name(filename: str, *, signed: bool) -> str:
    """The name the operator sees: ``form.pdf`` becomes ``form (draft).pdf`` or
    ``form (signed).pdf``."""
    suffix = f" ({'signed' if signed else 'draft'}).pdf"
    stem = filename[:-4] if filename.lower().endswith(".pdf") else filename
    # Shorten the stem before the label goes on, so the label is never what is cut.
    stem = casa_handoff.clean_filename(stem, "document")
    stem = stem[:casa_handoff.MAX_NAME_CHARS - len(suffix)]
    while len((stem + suffix).encode("utf-8")) > casa_handoff.MAX_NAME_BYTES:
        stem = stem[:-1]
    return casa_handoff.clean_filename(stem.rstrip() + suffix, "document" + suffix)


def _stage_in_outbox(data: bytes, ext: str) -> str:
    """Write ``data`` into Casa's plugin outbox under a fresh name and return
    the path. Written as a ``.part`` file and renamed, so Casa never claims a
    half-written file."""
    outbox = os.environ.get(OUTBOX_ENV) or DEFAULT_OUTBOX
    refusal = "Casa's plugin outbox {} cannot be written ({}); nothing was sent."
    try:
        fd, tmp = tempfile.mkstemp(dir=outbox, prefix=f".{PRODUCER}-", suffix=".part")
    except OSError as exc:
        raise DeliveryFailed(refusal.format(outbox, exc.strerror)) from None
    path = os.path.join(outbox, f"{PRODUCER}-{secrets.token_hex(8)}{ext}")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp, path)
    except OSError as exc:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise DeliveryFailed(refusal.format(outbox, exc.strerror)) from None
    return path


def _remove_published(path: str) -> None:
    """Remove a published handoff file and its now-empty directory."""
    with contextlib.suppress(OSError):
        os.unlink(path)
    with contextlib.suppress(OSError):
        os.rmdir(os.path.dirname(path))


def _deposit(path: str, *, kind: str, caption: str, filename: str,
             published: str | None = None) -> str:
    """Deposit the staged file; on a refusal remove it and the ``published`` handoff file."""
    try:
        return _deposit_file(FILE_SLOT, path, kind=kind, caption=caption, filename=filename)
    except casa_broker.DepositFailed as exc:
        with contextlib.suppress(OSError):
            os.unlink(path)
        if published is not None:
            _remove_published(published)
        raise DeliveryFailed(
            f"Casa did not accept the file for the chat ({exc.code}); nothing was sent."
            + (" The caption must be one short line." if exc.code == "bad_caption" else "")
        ) from None


def send_pdf(data: bytes, filename: str, *, signed: bool, caption: str = "") -> dict:
    """Publish ``data`` to the handoff folder under its chat name, stage it in
    the outbox and deposit it as a document. Returns the chat reference, the
    handoff path and the name the operator sees."""
    if len(data) > CHAT_MAX_BYTES:
        raise DeliveryFailed(
            f"The PDF is larger than {CHAT_MAX_BYTES // (1024 * 1024)} MB, the most Casa "
            "can send to the chat; nothing was sent.")
    name = chat_name(filename, signed=signed)
    try:
        handoff_path = casa_handoff.publish(PRODUCER, name, data=data)["path"]
    except casa_handoff.HandoffError as exc:
        raise DeliveryFailed(f"The handoff folder refused the file ({exc}); nothing was sent.") from None
    try:
        staged = _stage_in_outbox(data, ".pdf")
    except DeliveryFailed:
        _remove_published(handoff_path)
        raise
    reference = _deposit(staged, kind="document", caption=caption, filename=name,
                         published=handoff_path)
    return {"file": reference, "handoff_path": handoff_path, "sent_as": name}


def send_image(png: bytes, name: str, caption: str) -> dict:
    """Stage a PNG in the outbox and deposit it as a photo; nothing goes to handoff."""
    staged = _stage_in_outbox(png, ".png")
    reference = _deposit(staged, kind="photo", caption=caption, filename=name)
    return {"file": reference, "sent_as": name}
