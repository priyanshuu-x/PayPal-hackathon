"""Weak forensic signals. They add context and raise the risk score a little, but they NEVER
decide a verdict (plan section 7.3): metadata is easy to strip and forge, and AI-image detection
is unreliable. The record comparison in verify/ carries the result."""

from dataclasses import dataclass
from datetime import datetime, timedelta

from app.extraction.schema import ClaimedFacts
from app.forensics.metadata import FileMetadata, read_image_metadata, read_pdf_metadata
from app.verify.normalise import clean_text

MAX_FORENSIC_RISK = 15
PREDATE_MARGIN = timedelta(days=1)
_IMAGE_EDITORS = (
    "photoshop", "gimp", "canva", "figma", "pixelmator", "snapseed", "lightroom",
    "paint.net", "affinity", "picsart", "photopea", "illustrator",
)  # fmt: skip
_PDF_EDITORS = (
    "photoshop", "canva", "figma", "illustrator", "sejda", "smallpdf", "ilovepdf", "pdfescape",
)  # fmt: skip
_GENERATORS = (
    "stable diffusion", "midjourney", "dall-e", "dall·e", "firefly", "comfyui",
    "automatic1111", "imagen", "ai generated",
)  # fmt: skip


@dataclass(frozen=True)
class ForensicSignal:
    name: str
    weight: int  # 0 means informational
    note: str


@dataclass(frozen=True)
class ForensicReport:
    signals: tuple[ForensicSignal, ...]

    @property
    def risk(self) -> int:
        return min(sum(s.weight for s in self.signals), MAX_FORENSIC_RISK)

    @property
    def notes(self) -> tuple[str, ...]:
        return tuple(s.note for s in self.signals)


def _named(text: str | None, names: tuple[str, ...]) -> str | None:
    lowered = (text or "").lower()
    return next((n for n in names if n in lowered), None)


def signals_from_metadata(
    meta: FileMetadata,
    *,
    claimed: ClaimedFacts | None,
    order_created_at: datetime | None,
) -> ForensicReport:
    found: list[ForensicSignal] = []
    editors = _PDF_EDITORS if meta.kind == "pdf" else _IMAGE_EDITORS

    if editor := _named(meta.software, editors):
        found.append(
            ForensicSignal("editing_software", 5, f"The file was saved by {editor.title()}.")
        )
    if meta.generator_keys or _named(meta.software, _GENERATORS):
        found.append(
            ForensicSignal("generator_metadata", 6, "The file metadata names an image generator.")
        )
    if meta.has_c2pa:
        found.append(
            ForensicSignal(
                "content_credentials",
                0,
                "Content credentials (C2PA) are present but were not evaluated.",
            )
        )
    if (
        meta.kind == "image"
        and claimed is not None
        and claimed.document_type == "product_photo"
        and meta.camera is None
    ):
        found.append(ForensicSignal("no_camera_info", 3, "The photo has no camera information."))
    if meta.created_at and order_created_at and meta.created_at < order_created_at - PREDATE_MARGIN:
        found.append(
            ForensicSignal(
                "predates_order", 8, "The file says it was created before the order existed."
            )
        )
    if (
        meta.kind == "pdf"
        and meta.created_at
        and meta.modified_at
        and meta.modified_at - meta.created_at > timedelta(minutes=1)
    ):
        found.append(
            ForensicSignal(
                "modified_after_creation", 4, "The PDF was changed after it was created."
            )
        )

    return ForensicReport(
        tuple(ForensicSignal(s.name, s.weight, clean_text(s.note, 200) or s.name) for s in found)
    )


def analyze(
    data: bytes,
    mime: str,
    *,
    claimed: ClaimedFacts | None = None,
    order_created_at: datetime | None = None,
) -> ForensicReport:
    meta = read_pdf_metadata(data) if mime == "application/pdf" else read_image_metadata(data)
    return signals_from_metadata(meta, claimed=claimed, order_created_at=order_created_at)
