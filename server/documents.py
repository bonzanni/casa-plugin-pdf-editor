"""The document store: every PDF the plugin works on, as whole-file versions.

Layout::

    <root>/documents/<id>/meta.json     filename, live versions, highest number, creation time
    <root>/documents/<id>/v0.pdf        the original, as captured from the handoff folder
    <root>/documents/<id>/v1.pdf ...    one file per change

Version numbers only go up: ``undo`` drops the current version and the next
``add_version`` takes highest-ever + 1, so a number always means the same bytes.
Nothing here ever rewrites an existing version file.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import time
from dataclasses import dataclass

import pymupdf

import casa_handoff

RETENTION_S = 7 * 24 * 3600
MAX_BYTES = 25_000_000

_ID_RE = re.compile(r"^[0-9a-f]{16}$")
_STAGING = ".staging-"


class Refused(Exception):
    """A refusal; ``reason`` is a sentence for the assistant."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass
class Doc:
    id: str
    filename: str
    version: int
    signed: bool
    pages: int
    has_digital_signature: bool


def _write_atomic(path: str, data: bytes) -> None:
    """Write ``path`` through a temp file and ``os.replace``; on any failure the temp file
    goes too, so a failed write leaves no copy outside the recorded history."""
    tmp = path + ".tmp"
    try:
        with open(tmp, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= 0.01


def _check_pdf(data: bytes) -> tuple[int, bool]:
    """Return ``(pages, has_digital_signature)`` or raise :class:`Refused`."""
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception:
        raise Refused("The file is not a PDF, or it is too damaged to open.") from None
    try:
        if not doc.is_pdf:
            raise Refused("The file is not a PDF.")
        if doc.needs_pass or doc.is_encrypted:
            raise Refused("The PDF is protected by a password; ask for an unprotected copy.")
        if doc.page_count < 1:
            raise Refused("The PDF has no pages.")
        if doc.xref_get_key(doc.pdf_catalog(), "AcroForm/XFA")[0] != "null":
            raise Refused("The PDF is an XFA form, which this plugin cannot fill or show "
                          "reliably; ask for a flat or ordinary PDF version.")
        for page in doc:
            if page.rotation == 0:
                continue
            mb, cb = page.mediabox, page.cropbox
            if not (_close(mb.x0, 0) and _close(mb.y0, 0)
                    and all(_close(a, b) for a, b in zip(cb, mb))):
                raise Refused(
                    f"Page {page.number + 1} is rotated and its visible area does not start "
                    "at the page origin; this plugin cannot place marks on it reliably.")
            kind, unit = doc.xref_get_key(page.xref, "UserUnit")
            if kind != "null" and not (kind in ("int", "real") and _close(float(unit), 1)):
                raise Refused(
                    f"Page {page.number + 1} is rotated and uses a scaled page unit (UserUnit "
                    f"{unit}); this plugin cannot place marks on it reliably.")
        flags = doc.get_sigflags()
        return doc.page_count, flags > 0 and bool(flags & 1)
    finally:
        doc.close()


class Store:
    def __init__(self, root: str) -> None:
        self.root = root
        self._docs = os.path.join(root, "documents")
        os.makedirs(self._docs, exist_ok=True)

    # -- internal -----------------------------------------------------------

    def _dir(self, doc_id: str) -> str:
        if not isinstance(doc_id, str) or not _ID_RE.match(doc_id):
            raise Refused(f"There is no open document with id {doc_id!r}.")
        path = os.path.join(self._docs, doc_id)
        if not os.path.isfile(os.path.join(path, "meta.json")):
            raise Refused(f"There is no open document with id {doc_id!r}; it may have expired "
                          "after 7 days or been erased. Open the file again.")
        return path

    def _meta(self, doc_id: str) -> tuple[str, dict]:
        """The document's folder and recorded state. Every read of a document's state goes
        through here, so a crash's leftovers are gone before any tool uses the document."""
        path = self._dir(doc_id)
        with open(os.path.join(path, "meta.json"), encoding="utf-8") as fh:
            meta = json.load(fh)
        self._reconcile(path, meta)
        return path, meta

    @staticmethod
    def _reconcile(path: str, meta: dict) -> None:
        """Remove every file the history does not record: a version written but never recorded
        (a crash between the version's write and the metadata's), a version an interrupted
        undo dropped, and temp files. Version numbers come from ``meta['highest']``, so a
        removed number is never handed out again once recorded."""
        keep = {"meta.json"} | {f"v{v['n']}.pdf" for v in meta["versions"]}
        for name in os.listdir(path):
            if name not in keep:
                os.remove(os.path.join(path, name))

    @staticmethod
    def _save_meta(path: str, meta: dict) -> None:
        _write_atomic(os.path.join(path, "meta.json"), json.dumps(meta).encode("utf-8"))

    @staticmethod
    def _read(path: str, n: int) -> bytes:
        with open(os.path.join(path, f"v{n}.pdf"), "rb") as fh:
            return fh.read()

    # -- public -------------------------------------------------------------

    def open(self, handoff_path: str) -> Doc:
        self.purge_old()
        try:
            name, data = casa_handoff.capture(handoff_path)
        except casa_handoff.HandoffError as exc:
            raise Refused(f"That path is not a file in the handoff folder ({exc}).") from None
        if len(data) > MAX_BYTES:
            raise Refused(f"The file is larger than {MAX_BYTES} bytes.")
        pages, has_sig = _check_pdf(data)
        filename = casa_handoff.clean_filename(name, "document.pdf")

        doc_id = secrets.token_hex(8)
        staging = os.path.join(self._docs, _STAGING + doc_id)
        os.mkdir(staging)
        try:
            _write_atomic(os.path.join(staging, "v0.pdf"), data)
            self._save_meta(staging, {
                "filename": filename,
                "created": time.time(),
                "pages": pages,
                "has_digital_signature": has_sig,
                "highest": 0,
                "versions": [{"n": 0, "signed": False}],
            })
            os.rename(staging, os.path.join(self._docs, doc_id))
        except BaseException:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        return self.get(doc_id)

    def get(self, doc_id: str) -> Doc:
        _, meta = self._meta(doc_id)
        current = meta["versions"][-1]
        return Doc(id=doc_id, filename=meta["filename"], version=current["n"],
                   signed=current["signed"], pages=meta["pages"],
                   has_digital_signature=meta["has_digital_signature"])

    def add_version(self, doc_id: str, pdf_bytes: bytes, *, signed: bool = False) -> int:
        path, meta = self._meta(doc_id)
        n = meta["highest"] + 1
        version = os.path.join(path, f"v{n}.pdf")
        _write_atomic(version, pdf_bytes)
        meta["highest"] = n
        meta["versions"].append({"n": n, "signed": bool(signed)})
        try:
            self._save_meta(path, meta)
        except BaseException:   # no version file outside the history (it may be a signed copy)
            try:
                os.remove(version)
            except OSError:
                pass
            raise
        return n

    def undo(self, doc_id: str) -> int:
        path, meta = self._meta(doc_id)
        if len(meta["versions"]) < 2:
            raise Refused("There is nothing to undo: the document is at its original version.")
        dropped = meta["versions"].pop()
        self._save_meta(path, meta)
        try:
            os.remove(os.path.join(path, f"v{dropped['n']}.pdf"))
        except OSError:
            pass
        return meta["versions"][-1]["n"]

    def latest_bytes(self, doc_id: str) -> bytes:
        path, meta = self._meta(doc_id)
        return self._read(path, meta["versions"][-1]["n"])

    def latest_unsigned_bytes(self, doc_id: str) -> bytes:
        path, meta = self._meta(doc_id)
        n = next(v["n"] for v in reversed(meta["versions"]) if not v["signed"])
        return self._read(path, n)

    def purge_old(self, now: float | None = None) -> int:
        """Remove documents created more than :data:`RETENTION_S` ago; returns how
        many. Abandoned staging directories of the same age go too."""
        now = time.time() if now is None else now
        removed = 0
        for entry in os.scandir(self._docs):
            try:
                if entry.name.startswith(_STAGING):
                    if now - entry.stat(follow_symlinks=False).st_mtime > RETENTION_S:
                        shutil.rmtree(entry.path, ignore_errors=True)
                    continue
                with open(os.path.join(entry.path, "meta.json"), encoding="utf-8") as fh:
                    created = json.load(fh)["created"]
            except (OSError, ValueError, KeyError):
                continue
            if now - created > RETENTION_S:
                shutil.rmtree(entry.path, ignore_errors=True)
                removed += 1
        return removed

    def erase_documents(self) -> tuple[int, list[str]]:
        """Remove everything under ``<root>/documents/`` now, staging directories and
        stray files included. Returns how many documents were removed and the sorted
        names of the entries still there afterwards (empty when the erase is complete)."""
        removed = 0
        for entry in os.scandir(self._docs):
            is_dir = entry.is_dir(follow_symlinks=False)
            try:
                if is_dir:
                    shutil.rmtree(entry.path)
                else:
                    os.unlink(entry.path)
            except OSError:
                continue   # reported by what is still there below
            if is_dir and _ID_RE.match(entry.name):
                removed += 1
        return removed, sorted(os.listdir(self._docs))
