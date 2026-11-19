"""The signature: cleaning a photo of a handwritten signature, keeping it, and the two looks.

Cleaning is fixed image arithmetic, not a judgement: the paper's brightness is
estimated per tile, each pixel's alpha is how much darker it is than its local
paper, and the ink keeps its own colour. The tool layer shows the operator a
preview and stores the signature only after their approval (``SignatureStore``).

Both looks take displayed coordinates (see ``page_facts``): the handwritten
signature (the cleaned image) and a stamp (a framed box with the name in a
handwriting font). Either is placed with ``to_page_rect`` and
``rotate=page.rotation``, measured on PyMuPDF 1.28.2 to come out upright as
displayed for rotations 0, 90, 180 and 270. Form fields and annotations are then
baked into the page content first, so the mark is drawn over them.
"""
from __future__ import annotations

import hashlib
import os
import shutil
import tempfile

import pymupdf

from page_facts import _open_page, to_page_rect

_FONTS = os.path.join(os.path.dirname(__file__), "fonts")
_CAVEAT_FILE = os.path.join(_FONTS, "Caveat-Regular.ttf")
_DEJAVU_FILE = os.path.join(_FONTS, "DejaVuSans.ttf")
_CAVEAT = pymupdf.Font(fontfile=_CAVEAT_FILE)
_DEJAVU = pymupdf.Font(fontfile=_DEJAVU_FILE)

_WORK_MAX = 1600        # the photo is downscaled to this longest side before cleaning
_OUT_MAX = 1200         # the cleaned signature's longest side at most
_TILES = 25             # paper estimate: about this many tiles along the longest side
_PAPER_RANK = 0.9       # a tile's paper brightness: this rank of its sorted grey values
_SEGMENT = 8            # the paper estimate is interpolated in runs of this many pixels
_FAINT, _FULL = 0.15, 0.45   # relative darkness: transparent below _FAINT, opaque from _FULL
_MAX_BYTES = 30 * 2**20  # a larger photo is refused before decoding
_MIN_INK = 0.0005       # less ink than this fraction of the photo is a blank sheet
_MARGIN = 0.03          # crop margin, as a fraction of the ink's longest side

_BLUE = (0.16, 0.31, 0.66)
_INK = (0.10, 0.16, 0.42)      # the stamp's large name: dark blue ink
_CAPTION = "SIGNED ELECTRONICALLY"


def _alpha_table(paper: int) -> bytes:
    """grey value -> alpha for a pixel on paper of brightness ``paper``."""
    out = bytearray(256)
    if paper > 0:
        for g in range(paper):
            r = ((paper - g) / paper - _FAINT) / (_FULL - _FAINT)
            out[g] = 0 if r <= 0 else min(255, round(r * 255))
    return bytes(out)


_TABLES = [_alpha_table(p) for p in range(256)]


def _decode(photo: bytes, rotate: int) -> pymupdf.Pixmap:
    """The photo as RGB without alpha (transparency on white), upright as its EXIF
    orientation says, turned ``rotate`` degrees clockwise, longest side at most _WORK_MAX.

    Opened as an image document: unlike ``Pixmap(bytes)`` and ``insert_image``, that
    applies the EXIF orientation (measured on 1.28.2)."""
    if len(photo) > _MAX_BYTES:
        raise ValueError(f"The photo is too large ({len(photo) // 2**20} MB); send one under "
                         f"{_MAX_BYTES // 2**20} MB as JPEG or PNG.")
    unsupported = "This image format is not supported; send the signature photo as JPEG or PNG."
    doc = None
    try:
        doc = pymupdf.open(stream=photo, filetype="jpg")   # the type is sniffed; this is a hint
        if doc.is_pdf or doc.metadata.get("format") != "Image" or doc.page_count < 1:
            raise ValueError(unsupported)
        page = doc[0]
        info = page.get_image_info()
        longest_pt = max(page.rect.width, page.rect.height)
        longest_px = max(info[0]["width"], info[0]["height"]) if info else longest_pt
        if longest_pt <= 0 or longest_px < 1:
            raise ValueError("The image is empty; send the signature photo as JPEG or PNG.")
        scale = min(longest_px, _WORK_MAX) / longest_pt
        return page.get_pixmap(matrix=pymupdf.Matrix(scale, scale).prerotate(rotate), alpha=False)
    except (ValueError, MemoryError):
        raise
    except Exception:   # MuPDF's errors for bytes it cannot read as an image
        raise ValueError(unsupported) from None
    finally:
        if doc is not None:
            doc.close()


def _paper(grey: bytes, w: int, h: int) -> tuple[list[list[int]], int]:
    """Per-tile paper brightness (a high rank of each tile's grey values, then the
    brightest of each tile's 3x3 neighbourhood, so a tile full of ink takes its
    neighbours' paper) and the tile size."""
    tile = max(8, -(-max(w, h) // _TILES))
    tx, ty = -(-w // tile), -(-h // tile)
    rank = []
    for j in range(ty):
        rows = range(j * tile, min(h, (j + 1) * tile), 2)
        line = []
        for i in range(tx):
            x0, x1 = i * tile, min(w, (i + 1) * tile)
            values = sorted(b"".join(grey[y * w + x0:y * w + x1:2] for y in rows))
            line.append(values[int(_PAPER_RANK * (len(values) - 1))])
        rank.append(line)
    paper = [[max(rank[b][a] for b in range(max(0, j - 1), min(ty, j + 2))
                  for a in range(max(0, i - 1), min(tx, i + 2)))
              for i in range(tx)] for j in range(ty)]
    return paper, tile


def _weights(n: int, tile: int, count: int) -> tuple[int, int, float]:
    """Bilinear lookup of position ``n`` (a pixel centre) between tile centres."""
    f = min(max((n + 0.5) / tile - 0.5, 0.0), count - 1.0)
    i0 = int(f)
    return i0, min(i0 + 1, count - 1), f - i0


def _alpha(grey: bytes, w: int, h: int) -> bytes:
    paper, tile = _paper(grey, w, h)
    ty, tx = len(paper), len(paper[0])
    segments = [(x0, min(w, x0 + _SEGMENT), *_weights(x0 + _SEGMENT // 2, tile, tx))
                for x0 in range(0, w, _SEGMENT)]
    out = bytearray(w * h)
    for y in range(h):
        j0, j1, fy = _weights(y, tile, ty)
        column = [a + (b - a) * fy for a, b in zip(paper[j0], paper[j1])]
        base = y * w
        for x0, x1, i0, i1, fx in segments:
            p = round(column[i0] + (column[i1] - column[i0]) * fx)
            out[base + x0:base + x1] = grey[base + x0:base + x1].translate(_TABLES[p])
    return bytes(out)


def _ink_box(alpha: bytes, w: int, h: int) -> tuple[int, int, int, int] | None:
    top = bottom = left = right = None
    for y in range(h):
        row = alpha[y * w:(y + 1) * w]
        rest = row.lstrip(b"\0")
        if not rest:
            continue
        l, r = w - len(rest), len(row.rstrip(b"\0"))
        top = y if top is None else top
        bottom = y + 1
        left = l if left is None else min(left, l)
        right = r if right is None else max(right, r)
    return None if top is None else (left, top, right, bottom)


def clean(photo: bytes, *, rotate: int = 0) -> bytes:
    """PNG with alpha of the ink in ``photo``, the paper made transparent, cropped to the
    ink with a small margin, longest side at most 1200 px. ``rotate`` (0, 90, 180 or 270)
    turns the photo clockwise after decoding, for a photo that came out sideways."""
    if isinstance(rotate, bool) or rotate not in (0, 90, 180, 270):
        raise ValueError(f"rotate must be 0, 90, 180 or 270 (degrees clockwise), not {rotate!r}.")
    rgb = _decode(photo, rotate)
    w, h = rgb.width, rgb.height
    alpha = _alpha(pymupdf.Pixmap(pymupdf.csGRAY, rgb).samples, w, h)
    box = _ink_box(alpha, w, h)
    if box is None or w * h - alpha.count(0) < _MIN_INK * w * h:
        raise ValueError("No signature found in the photo: it looks like a blank sheet. "
                         "Send a photo of the signature in dark ink on plain paper.")
    left, top, right, bottom = box
    m = max(4, round(_MARGIN * max(right - left, bottom - top)))
    left, top, right, bottom = max(0, left - m), max(0, top - m), min(w, right + m), min(h, bottom + m)
    cw, ch = right - left, bottom - top
    samples = rgb.samples
    crop = pymupdf.Pixmap(pymupdf.csRGB, cw, ch, b"".join(
        samples[(y * w + left) * 3:(y * w + right) * 3] for y in range(top, bottom)), False)
    out = pymupdf.Pixmap(crop, 1)
    out.set_alpha(b"".join(alpha[y * w + left:y * w + right] for y in range(top, bottom)))
    scale = _OUT_MAX / max(cw, ch)
    if scale < 1:
        out = pymupdf.Pixmap(out, max(1, round(cw * scale)), max(1, round(ch * scale)))
    return out.tobytes("png")


def aspect(sig_png: bytes) -> float:
    """The signature image's height / width."""
    pix = pymupdf.Pixmap(sig_png)
    return pix.height / pix.width


def preview(cleaned: bytes) -> bytes:
    """PNG of a white card with a dotted line and the signature resting on it."""
    card_w, card_h, pad, line_y = 600, 260, 40, 210
    a = aspect(cleaned)
    width = min(card_w - 2 * pad, (line_y - pad) / a)
    height = width * a
    x0 = (card_w - width) / 2
    sheet = pymupdf.open()
    try:
        page = sheet.new_page(width=card_w, height=card_h)
        page.draw_line((pad, line_y), (card_w - pad, line_y), color=(0.6, 0.6, 0.6),
                       width=1, dashes="[2 3] 0")
        page.insert_image(pymupdf.Rect(x0, line_y - height, x0 + width, line_y), stream=cleaned)
        return page.get_pixmap(matrix=pymupdf.Matrix(1.5, 1.5), alpha=False).tobytes("png")
    finally:
        sheet.close()


def _preview_id(cleaned: bytes) -> str:
    return hashlib.sha256(cleaned).hexdigest()[:16]


class SignatureStore:
    """The stored signature and the one pending preview, under ``<root>/signature/``."""

    def __init__(self, root: str):
        self._dir = os.path.join(root, "signature")
        self._stored = os.path.join(self._dir, "signature.png")
        self._pending = os.path.join(self._dir, "pending.png")

    @staticmethod
    def _read(path: str) -> bytes | None:
        try:
            with open(path, "rb") as f:
                return f.read()
        except FileNotFoundError:
            return None

    def set_pending(self, cleaned: bytes) -> str:
        """Hold ``cleaned`` as the pending preview (replacing any earlier one); its preview id."""
        self._write(self._pending, cleaned)
        return _preview_id(cleaned)

    def _write(self, path: str, data: bytes) -> None:
        """Write ``data`` to ``path`` atomically: a 0600 temp file, then ``os.replace``."""
        os.makedirs(self._dir, mode=0o700, exist_ok=True)
        os.chmod(self._dir, 0o700)
        fd, tmp = tempfile.mkstemp(dir=self._dir, prefix=".write-", suffix=".tmp")   # mode 0600
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def commit(self, preview_id: str) -> bool:
        """Store the pending signature if its id is ``preview_id``; False otherwise.

        Stores the bytes it checked, never whatever the pending file holds by then: a
        ``set_pending`` landing meanwhile stays pending and is not stored."""
        pending = self._read(self._pending)
        if pending is None or _preview_id(pending) != preview_id:
            return False
        self._write(self._stored, pending)
        if self._read(self._pending) == pending:
            try:
                os.unlink(self._pending)
            except FileNotFoundError:
                pass
        return True

    def get(self) -> bytes | None:
        return self._read(self._stored)

    def remove(self) -> bool:
        """Delete the stored signature and any pending preview; whether one was stored."""
        existed = False
        for path in (self._stored, self._pending):
            try:
                os.unlink(path)
                existed = existed or path == self._stored
            except FileNotFoundError:
                pass
        return existed

    def erase(self) -> tuple[bool, list[str]]:
        """Remove everything under ``<root>/signature/``: the stored signature, the pending
        preview and any temp file a crash left. Returns whether a stored signature was
        removed and the sorted names of the entries still there (empty when complete)."""
        if not os.path.isdir(self._dir):
            return False, []
        had = os.path.isfile(self._stored)
        for entry in os.scandir(self._dir):
            try:
                if entry.is_dir(follow_symlinks=False):
                    shutil.rmtree(entry.path)
                else:
                    os.unlink(entry.path)
            except OSError:
                continue   # reported by what is still there below
        left = sorted(os.listdir(self._dir))
        return had and os.path.basename(self._stored) not in left, left

    def status(self) -> dict:
        stored = self.get()
        return {"stored": stored is not None, "pending": os.path.isfile(self._pending),
                "aspect": None if stored is None else round(aspect(stored), 2)}


def _place(page: pymupdf.Page, x: float, y: float,
           width: float, height: float, what: str) -> pymupdf.Rect:
    """The unrotated rectangle for a displayed box with bottom-left (x, y); refuses one
    that is not fully on the page."""
    if not width > 0:
        raise ValueError(f"The {what} width must be greater than 0, not {width!r}.")
    shown = pymupdf.Rect(x, y - height, x + width, y)
    if not page.rect.contains(shown):
        raise ValueError(
            f"The {what} would reach outside the page: it would cover x {shown.x0:.1f} to "
            f"{shown.x1:.1f}, y {shown.y0:.1f} to {shown.y1:.1f}, and the page is "
            f"{page.rect.width:.1f} x {page.rect.height:.1f} points as displayed.")
    return to_page_rect(page, shown.x0, shown.y0, shown.x1, shown.y1)


def draw_handwritten(pdf: bytes, sig_png: bytes, *, page: int, x: float, y: float, width: float) -> bytes:
    """``sig_png`` with its bottom-left at displayed (x, y), ``width`` points wide, aspect
    kept, upright as displayed; fields and annotations baked. New bytes; ``pdf`` is untouched."""
    doc, pg = _open_page(pdf, page)
    try:
        rect = _place(pg, x, y, width, width * aspect(sig_png), "signature")
        rotation = pg.rotation
        doc.bake()   # first: baked field appearances are appended to the content, over the mark
        doc[page - 1].insert_image(rect, stream=sig_png, rotate=rotation)
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()


def _missing(text: str, font: pymupdf.Font) -> list[str]:
    return sorted({c for c in text if not font.has_glyph(ord(c))})


def _stamp(width: float, height: float, signer_name: str, when: str) -> pymupdf.Document:
    """The stamp as a one-page PDF of ``width`` x ``height`` points, drawn upright."""
    for label, text in (("name", signer_name), ("time", when)):
        if not text.strip():
            raise ValueError(f"The {label} for the stamp is empty.")
        missing = _missing(text, _DEJAVU)   # both are also written small in DejaVu Sans
        if missing:
            raise ValueError(f"The stamp cannot write these characters of the {label}: {' '.join(missing)}")
    name_font, name_file = (_DEJAVU, _DEJAVU_FILE) if _missing(signer_name, _CAVEAT) else (_CAVEAT, _CAVEAT_FILE)

    sheet = pymupdf.open()
    page = sheet.new_page(width=width, height=height)
    # The stamp is its own sheet, placed with show_pdf_page as a form XObject with its own
    # resources, so the target page's font names cannot override these.
    page.insert_font(fontname="dvs", fontfile=_DEJAVU_FILE)
    page.insert_font(fontname="sig", fontfile=name_file)

    cap = height * 0.11
    top = cap * 0.6 + 0.5                       # the frame's top line runs through the caption
    page.draw_rect(pymupdf.Rect(0.5, top, width - 0.5, height - 0.5), color=_BLUE, width=1,
                   radius=0.06)
    cap_x, cap_len = 0.06 * width, _DEJAVU.text_length(_CAPTION, cap)
    page.draw_rect(pymupdf.Rect(cap_x - 2, top - cap * 0.6, cap_x + cap_len + 2, top + cap * 0.6),
                   color=None, fill=(1, 1, 1))
    page.insert_text((cap_x, top + cap * 0.36), _CAPTION, fontname="dvs", fontsize=cap, color=_BLUE)

    size = min(width * 2 / 3 / name_font.text_length(signer_name, 1), height * 0.42)
    centre = (top + height) / 2
    baseline = centre + (name_font.ascender + name_font.descender) / 2 * size
    page.insert_text(((width - name_font.text_length(signer_name, size)) / 2, baseline),
                     signer_name, fontname="sig", fontsize=size, color=_INK)

    small, inset = height * 0.09, width * 0.03
    for text, base in ((signer_name, height - inset - small * 1.25), (when, height - inset)):
        page.insert_text((width - inset - _DEJAVU.text_length(text, small), base), text,
                         fontname="dvs", fontsize=small, color=_BLUE)
    sheet.subset_fonts()   # only the sheet's own two fonts; the stamp is then about 50 KB, not 570
    return sheet


def draw_stamp(pdf: bytes, *, page: int, x: float, y: float, width: float,
               signer_name: str, when: str) -> bytes:
    """A framed stamp, bottom-left at displayed (x, y), ``width`` x ``width / 3`` points:
    caption, the name in Caveat, and the name and ``when`` small at the bottom right.
    Upright as displayed; fields and annotations baked. New bytes; ``pdf`` is untouched."""
    doc, pg = _open_page(pdf, page)
    try:
        rect = _place(pg, x, y, width, width / 3, "stamp")
        rotation = pg.rotation
        stamp = _stamp(width, width / 3, signer_name, when)
        try:
            doc.bake()   # first: baked field appearances are appended to the content, over the mark
            doc[page - 1].show_pdf_page(rect, stamp, 0, rotate=rotation)
        finally:
            stamp.close()
        return doc.tobytes(garbage=3, deflate=True)
    finally:
        doc.close()
