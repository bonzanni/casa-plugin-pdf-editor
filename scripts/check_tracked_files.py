"""Fail if a document or image file is tracked outside the vendored libraries and fonts.

Test documents and photos are generated in code (tests/fixtures.py), so a tracked PDF
or image anywhere else is most likely a real form or signature committed by accident.
A file counts by its extension (any case) or by its first bytes. This says nothing
about personal data in text files.

Run from anywhere inside the repository: python scripts/check_tracked_files.py
"""
from __future__ import annotations

import os
import subprocess
import sys

ALLOWED_PREFIXES = ("server/vendor/", "server/fonts/")
EXTENSIONS = {".pdf", ".png", ".jpg", ".jpeg", ".heic", ".gif", ".tif", ".tiff",
              ".bmp", ".webp"}
SIGNATURES = (b"\x89PNG\r\n\x1a\n", b"\xff\xd8\xff", b"GIF8", b"II*\0", b"MM\0*")
HEIF_BRANDS = (b"heic", b"heix", b"hevc", b"mif1", b"msf1", b"heif")
PDF_WINDOW = 1024   # readers accept the PDF header anywhere in the first 1024 bytes


def _is_document(head: bytes, tail: bytes) -> bool:
    """By the first bytes; a PDF header after leading junk counts when the file also ends
    with %%EOF (so source files that merely mention the header do not)."""
    return (head.startswith(SIGNATURES) or head.startswith(b"%PDF")
            or (head[:4] == b"RIFF" and head[8:12] == b"WEBP")
            or (head[4:8] == b"ftyp" and head[8:12] in HEIF_BRANDS)
            or (head[:2] == b"BM" and head[6:10] == b"\0\0\0\0")
            or (b"%PDF-" in head and b"%%EOF" in tail))


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True,
                          text=True).stdout


def offending(root: str, paths: list[str]) -> list[str]:
    found = []
    for rel in paths:
        if rel.startswith(ALLOWED_PREFIXES):
            continue
        if os.path.splitext(rel)[1].lower() in EXTENSIONS:
            found.append(rel)
            continue
        full = os.path.join(root, rel)
        if not os.path.isfile(full) or os.path.islink(full):
            continue
        with open(full, "rb") as fh:
            head = fh.read(PDF_WINDOW)
            fh.seek(max(0, os.path.getsize(full) - PDF_WINDOW))
            tail = fh.read()
        if _is_document(head, tail):
            found.append(rel)
    return found


def main() -> int:
    root = _git("rev-parse", "--show-toplevel").strip()
    paths = [p for p in _git("-C", root, "ls-files", "-z").split("\0") if p]
    found = offending(root, paths)
    for rel in found:
        print(f"tracked document or image outside server/vendor/ and server/fonts/: {rel}")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
