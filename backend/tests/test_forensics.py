import io
from datetime import UTC, datetime, timedelta

import pytest
from PIL import Image, PngImagePlugin
from pypdf import PdfWriter

from app.extraction.schema import ClaimedFacts
from app.forensics.metadata import read_image_metadata, read_pdf_metadata
from app.forensics.signals import MAX_FORENSIC_RISK, ForensicReport, ForensicSignal, analyze

ORDER_TIME = datetime(2026, 11, 3, 10, 15, tzinfo=UTC)


def _jpeg(software: str | None = None, make: str | None = None, taken: str | None = None) -> bytes:
    exif = Image.Exif()
    if software:
        exif[0x0131] = software
    if make:
        exif[0x010F] = make
        exif[0x0110] = "Model X"
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
        exif[0x8769] = exif.get_ifd(0x8769)
    out = io.BytesIO()
    Image.new("RGB", (20, 20), (9, 9, 9)).save(out, format="JPEG", exif=exif)
    return out.getvalue()


def _png(**text: str) -> bytes:
    info = PngImagePlugin.PngInfo()
    for key, value in text.items():
        info.add_text(key, value)
    out = io.BytesIO()
    Image.new("RGB", (20, 20)).save(out, format="PNG", pnginfo=info)
    return out.getvalue()


def _pdf(
    producer: str | None = None, created: str | None = None, modified: str | None = None
) -> bytes:
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    meta = {}
    if producer:
        meta["/Producer"] = producer
        meta["/Creator"] = "Writer"
    if created:
        meta["/CreationDate"] = created
    if modified:
        meta["/ModDate"] = modified
    if meta:
        writer.add_metadata(meta)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _names(report: ForensicReport) -> set[str]:
    return {s.name for s in report.signals}


def test_clean_camera_photo_has_no_signals() -> None:
    claimed = ClaimedFacts(document_type="product_photo")
    report = analyze(
        _jpeg(make="Canon", taken="2026:11:10 09:00:00"),
        "image/jpeg",
        claimed=claimed,
        order_created_at=ORDER_TIME,
    )
    assert report.signals == () and report.risk == 0 and report.notes == ()


def test_editing_software_in_exif_is_flagged() -> None:
    report = analyze(_jpeg(software="Adobe Photoshop 25.1"), "image/jpeg")
    assert _names(report) == {"editing_software"}
    assert "Photoshop" in report.notes[0]
    assert report.risk == 5


def test_png_software_text_chunk_is_read() -> None:
    assert "editing_software" in _names(analyze(_png(Software="Canva"), "image/png"))
    meta = read_image_metadata(_png(Comment="made with GIMP"))
    assert meta.software == "made with GIMP"


def test_generator_metadata_is_flagged() -> None:
    assert "generator_metadata" in _names(
        analyze(_png(parameters="a cute cat, Steps: 20"), "image/png")
    )
    assert "generator_metadata" in _names(analyze(_jpeg(software="Midjourney v6"), "image/jpeg"))


def test_c2pa_is_informational_only() -> None:
    report = analyze(_jpeg() + b"jumbc2pa manifest", "image/jpeg")
    assert _names(report) == {"content_credentials"} and report.risk == 0


def test_a_photo_without_camera_info_is_flagged_but_a_receipt_is_not() -> None:
    photo = ClaimedFacts(document_type="product_photo")
    receipt = ClaimedFacts(document_type="receipt")
    assert "no_camera_info" in _names(analyze(_png(), "image/png", claimed=photo))
    assert "no_camera_info" not in _names(analyze(_png(), "image/png", claimed=receipt))
    assert "no_camera_info" not in _names(analyze(_png(), "image/png", claimed=None))
    assert "no_camera_info" not in _names(analyze(_jpeg(make="Canon"), "image/jpeg", claimed=photo))


def test_file_dated_before_the_order_is_flagged() -> None:
    old = _jpeg(taken="2026:10:01 12:00:00")
    assert "predates_order" in _names(analyze(old, "image/jpeg", order_created_at=ORDER_TIME))
    within_margin = _jpeg(taken="2026:11:02 12:00:00")
    assert "predates_order" not in _names(
        analyze(within_margin, "image/jpeg", order_created_at=ORDER_TIME)
    )
    assert "predates_order" not in _names(analyze(old, "image/jpeg", order_created_at=None))


def test_garbage_exif_dates_are_ignored() -> None:
    assert read_image_metadata(_jpeg(taken="not a date")).created_at is None


def test_unreadable_image_gives_empty_metadata() -> None:
    meta = read_image_metadata(b"not an image")
    assert meta.kind == "image" and meta.software is None and not meta.has_c2pa
    assert analyze(b"not an image", "image/png").signals == ()


def test_pdf_editor_and_modified_after_creation() -> None:
    data = _pdf(
        producer="Sejda PDF Editor", created="D:20261103101500Z", modified="D:20261104101500Z"
    )
    report = analyze(data, "application/pdf", order_created_at=ORDER_TIME)
    assert _names(report) == {"editing_software", "modified_after_creation"}
    assert report.risk == 9


def test_pdf_created_before_the_order_is_flagged() -> None:
    report = analyze(
        _pdf(created="D:20260101000000Z"), "application/pdf", order_created_at=ORDER_TIME
    )
    assert "predates_order" in _names(report)


def test_plain_pdf_and_pdf_without_metadata_are_quiet() -> None:
    assert (
        analyze(
            _pdf(created="D:20261103101500Z", modified="D:20261103101500Z"),
            "application/pdf",
            order_created_at=ORDER_TIME,
        ).signals
        == ()
    )
    meta = read_pdf_metadata(_pdf())
    assert meta.kind == "pdf" and meta.software == "pypdf"  # the writer names itself


def test_naive_pdf_dates_are_treated_as_utc_and_bad_ones_ignored() -> None:
    meta = read_pdf_metadata(_pdf(producer="x", created="D:20261103101500"))
    assert meta.created_at is not None and meta.created_at.tzinfo is UTC
    bad = read_pdf_metadata(_pdf(created="D:garbage"))
    assert bad.created_at is None


def test_broken_pdf_gives_empty_metadata() -> None:
    assert read_pdf_metadata(b"%PDF-1.7 junk").software is None


def test_risk_is_capped_and_notes_are_clean_text() -> None:
    heavy = ForensicReport(tuple(ForensicSignal(f"s{i}", 8, "n") for i in range(5)))
    assert heavy.risk == MAX_FORENSIC_RISK
    report = analyze(
        _png(parameters="x", Software="Photoshop"), "image/png", order_created_at=ORDER_TIME
    )
    assert all(n.isprintable() for n in report.notes)


@pytest.mark.parametrize("offset", [timedelta(days=2), timedelta(days=30)])
def test_predate_margin_is_one_day(offset: timedelta) -> None:
    taken = (ORDER_TIME - offset).strftime("%Y:%m:%d %H:%M:%S")
    assert "predates_order" in _names(
        analyze(_jpeg(taken=taken), "image/jpeg", order_created_at=ORDER_TIME)
    )
