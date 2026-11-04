"""Upload validation and private storage. Uploaded files are hostile input.

Checks: size cap, magic bytes (the filename and client MIME type are ignored), pixel cap against
decompression bombs, PDF page cap, no encrypted or active-content PDFs. Files are stored under a
random key outside any public path, with owner-only permissions.
"""

import hashlib
import io
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError
from pypdf import PdfReader

MAX_BYTES = 10 * 1024 * 1024
MAX_PIXELS = 40_000_000
MAX_PDF_PAGES = 20
PREVIEW_MAX_SIDE = 2000

_PDF_ACTIVE_MARKERS = (b"/JavaScript", b"/JS", b"/Launch", b"/OpenAction", b"/EmbeddedFile")
_KEY_RE = re.compile(r"^[0-9a-f]{32}$")
_PIL_FORMAT_BY_MIME = {"image/png": "PNG", "image/jpeg": "JPEG", "image/webp": "WEBP"}


class UploadRejected(Exception):
    """The file is not acceptable. The message is safe to show to the uploader."""


@dataclass(frozen=True)
class ValidatedUpload:
    mime: str
    size: int
    sha256: str
    pages: int | None


def sniff_mime(data: bytes) -> str | None:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data.startswith(b"%PDF-"):
        return "application/pdf"
    return None


def validate_upload(data: bytes) -> ValidatedUpload:
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > MAX_BYTES:
        raise UploadRejected("The file is larger than 10 MB.")
    mime = sniff_mime(data)
    if mime is None:
        raise UploadRejected("Only PNG, JPEG, WebP or PDF files are accepted.")

    pages: int | None = None
    if mime == "application/pdf":
        pages = _check_pdf(data)
    else:
        _check_image(data, mime)
    return ValidatedUpload(
        mime=mime, size=len(data), sha256=hashlib.sha256(data).hexdigest(), pages=pages
    )


def _check_image(data: bytes, mime: str) -> None:
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.format != _PIL_FORMAT_BY_MIME[mime]:
                raise UploadRejected("The file content does not match its type.")
            width, height = img.size
            if width * height > MAX_PIXELS:
                raise UploadRejected("The image dimensions are too large.")
            img.verify()
    except UploadRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise UploadRejected("The image could not be read.") from None


def _check_pdf(data: bytes) -> int:
    if any(marker in data for marker in _PDF_ACTIVE_MARKERS):
        raise UploadRejected("PDFs with scripts or embedded files are not accepted.")
    try:
        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted:
            raise UploadRejected("Encrypted PDFs are not accepted.")
        pages = len(reader.pages)
    except UploadRejected:
        raise
    except Exception:  # pypdf raises many types on malformed input
        raise UploadRejected("The PDF could not be read.") from None
    if pages < 1:
        raise UploadRejected("The PDF has no pages.")
    if pages > MAX_PDF_PAGES:
        raise UploadRejected(f"The PDF has more than {MAX_PDF_PAGES} pages.")
    return pages


def store_file(upload_dir: Path, data: bytes) -> str:
    """Write the bytes under a random key (no extension, no client-supplied name)."""
    upload_dir.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(16)
    fd = os.open(upload_dir / key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
    return key


def read_file(upload_dir: Path, key: str) -> bytes:
    if not _KEY_RE.fullmatch(key):
        raise FileNotFoundError(key)
    return (upload_dir / key).read_bytes()


def reencode_preview(data: bytes) -> bytes:
    """PNG re-encode for display: drops all metadata and caps the size. Images only."""
    try:
        with Image.open(io.BytesIO(data)) as img:
            if img.width * img.height > MAX_PIXELS:
                raise UploadRejected("The image dimensions are too large.")
            img.load()
            clean = ImageOps.exif_transpose(img) or img
            clean = clean.convert("RGBA" if "A" in clean.getbands() else "RGB")
            clean.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
            out = io.BytesIO()
            clean.save(out, format="PNG")
            return out.getvalue()
    except UploadRejected:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError):
        raise UploadRejected("The image could not be read.") from None
