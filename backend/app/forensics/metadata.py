"""Read file metadata (EXIF, PNG text chunks, PDF info). Metadata is easy to strip or forge, so
everything built on it is weak evidence."""

import io
from dataclasses import dataclass, field
from datetime import UTC, datetime

from PIL import Image
from pypdf import PdfReader

_EXIF_SOFTWARE = 0x0131
_EXIF_MAKE = 0x010F
_EXIF_MODEL = 0x0110
_EXIF_DATETIME = 0x0132
_EXIF_IFD = 0x8769
_EXIF_DATETIME_ORIGINAL = 0x9003
_PNG_TEXT_KEYS = ("software", "comment", "description", "parameters", "prompt", "workflow")


@dataclass(frozen=True)
class FileMetadata:
    kind: str  # "image" or "pdf"
    software: str | None = None  # software, producer or creator named in the file
    camera: str | None = None  # EXIF make and model
    created_at: datetime | None = None  # timezone-aware UTC when the file says it was made
    modified_at: datetime | None = None
    generator_keys: tuple[str, ...] = field(default_factory=tuple)  # e.g. PNG "parameters"
    has_c2pa: bool = False


def _parse_exif_time(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.strptime(value.strip(), "%Y:%m:%d %H:%M:%S").replace(tzinfo=UTC)
    except ValueError:
        return None


def read_image_metadata(data: bytes) -> FileMetadata:
    try:
        with Image.open(io.BytesIO(data)) as img:
            exif = img.getexif()
            info = {str(k).lower(): v for k, v in img.info.items()}
            software = exif.get(_EXIF_SOFTWARE)
            for key in _PNG_TEXT_KEYS:
                if not software and isinstance(info.get(key), str):
                    software = info[key]
            make, model = exif.get(_EXIF_MAKE), exif.get(_EXIF_MODEL)
            camera = " ".join(str(p).strip() for p in (make, model) if p) or None
            taken = _parse_exif_time(exif.get_ifd(_EXIF_IFD).get(_EXIF_DATETIME_ORIGINAL))
            modified = _parse_exif_time(exif.get(_EXIF_DATETIME))
            generators = tuple(k for k in ("parameters", "prompt", "workflow") if k in info)
    except (OSError, ValueError, SyntaxError):
        return FileMetadata(kind="image")
    return FileMetadata(
        kind="image",
        software=str(software) if software else None,
        camera=camera,
        created_at=taken or modified,
        modified_at=modified,
        generator_keys=generators,
        has_c2pa=b"c2pa" in data,
    )


def read_pdf_metadata(data: bytes) -> FileMetadata:
    try:
        meta = PdfReader(io.BytesIO(data), strict=False).metadata
    except Exception:  # pypdf raises many types on malformed files
        return FileMetadata(kind="pdf")
    if meta is None:
        return FileMetadata(kind="pdf")
    software = " / ".join(str(v) for v in (meta.producer, meta.creator) if v) or None

    def aware(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)

    try:
        created, modified = aware(meta.creation_date), aware(meta.modification_date)
    except Exception:  # an unparseable date string
        created = modified = None
    return FileMetadata(kind="pdf", software=software, created_at=created, modified_at=modified)
