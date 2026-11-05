import io
import os
import stat
from pathlib import Path

import pytest
from PIL import Image
from pypdf import PdfWriter

from app.security.uploads import (
    MAX_BYTES,
    MAX_PDF_PAGES,
    UploadRejected,
    read_file,
    reencode_preview,
    sniff_mime,
    store_file,
    validate_upload,
)
from tests.conftest import png_bytes


def _pdf(pages: int = 1) -> bytes:
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _image(fmt: str) -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (10, 10), (1, 2, 3)).save(out, format=fmt)
    return out.getvalue()


@pytest.mark.parametrize(
    ("data", "mime"),
    [
        (_image("PNG"), "image/png"),
        (_image("JPEG"), "image/jpeg"),
        (_image("WEBP"), "image/webp"),
        (_pdf(), "application/pdf"),
    ],
)
def test_valid_files_are_accepted(data: bytes, mime: str) -> None:
    result = validate_upload(data)
    assert result.mime == mime
    assert result.size == len(data)
    assert len(result.sha256) == 64


def test_pdf_page_count_is_reported() -> None:
    assert validate_upload(_pdf(3)).pages == 3


def test_empty_file_rejected() -> None:
    with pytest.raises(UploadRejected):
        validate_upload(b"")


def test_oversized_file_rejected() -> None:
    with pytest.raises(UploadRejected, match="10 MB"):
        validate_upload(b"\x89PNG\r\n\x1a\n" + b"0" * MAX_BYTES)


def test_wrong_magic_bytes_rejected() -> None:
    for data in (b"MZ\x90\x00 an executable", b"<html><script>x</script></html>", b"GIF89a....."):
        with pytest.raises(UploadRejected, match="Only PNG"):
            validate_upload(data)


def test_sniff_ignores_everything_but_the_bytes() -> None:
    assert sniff_mime(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "image/webp"
    assert sniff_mime(b"RIFF\x00\x00\x00\x00WAVEfmt ") is None


def test_png_header_with_garbage_body_rejected() -> None:
    with pytest.raises(UploadRejected, match="could not be read"):
        validate_upload(b"\x89PNG\r\n\x1a\n" + b"garbage" * 50)


def test_content_that_contradicts_the_magic_is_rejected() -> None:
    # JPEG signature in front of a real PNG body is not a decodable JPEG
    with pytest.raises(UploadRejected):
        validate_upload(b"\xff\xd8\xff" + png_bytes())


def test_decompression_bomb_rejected() -> None:
    # 1-bit 8000 x 8000 (64 MP) compresses to a few KB but would need a huge pixel buffer
    out = io.BytesIO()
    Image.new("1", (8000, 8000)).save(out, format="PNG")
    assert len(out.getvalue()) < MAX_BYTES
    with pytest.raises(UploadRejected, match="dimensions"):
        validate_upload(out.getvalue())


def test_pdf_page_cap() -> None:
    assert validate_upload(_pdf(MAX_PDF_PAGES)).pages == MAX_PDF_PAGES
    with pytest.raises(UploadRejected, match="pages"):
        validate_upload(_pdf(MAX_PDF_PAGES + 1))


def test_encrypted_pdf_rejected() -> None:
    writer = PdfWriter()
    writer.add_blank_page(width=10, height=10)
    writer.encrypt("secret")
    out = io.BytesIO()
    writer.write(out)
    with pytest.raises(UploadRejected, match="Encrypted"):
        validate_upload(out.getvalue())


def test_pdf_with_javascript_rejected() -> None:
    data = _pdf().replace(
        b"%%EOF", b"1 0 obj << /S /JavaScript /JS (app.alert(1)) >> endobj\n%%EOF"
    )
    with pytest.raises(UploadRejected, match="scripts"):
        validate_upload(data)


def test_truncated_pdf_rejected() -> None:
    with pytest.raises(UploadRejected):
        validate_upload(b"%PDF-1.7\n1 0 obj\n")


def test_store_uses_random_private_key(tmp_path: Path) -> None:
    key_a = store_file(tmp_path / "up", b"one")
    key_b = store_file(tmp_path / "up", b"one")
    assert key_a != key_b
    assert len(key_a) == 32 and "." not in key_a
    assert read_file(tmp_path / "up", key_a) == b"one"
    if os.name != "nt":  # Windows has no POSIX modes
        assert stat.S_IMODE((tmp_path / "up" / key_a).stat().st_mode) == 0o600


@pytest.mark.parametrize("key", ["../secret", "..\\x", "a" * 31, "A" * 32, "x" * 33, ""])
def test_read_file_rejects_path_tricks(tmp_path: Path, key: str) -> None:
    with pytest.raises(FileNotFoundError):
        read_file(tmp_path, key)


def test_preview_is_png_without_metadata() -> None:
    original = png_bytes(exif=True)
    assert b"SecretCameraMaker" in original
    preview = reencode_preview(original)
    assert preview.startswith(b"\x89PNG")
    assert b"SecretCameraMaker" not in preview
    with Image.open(io.BytesIO(preview)) as img:
        assert not img.getexif()


def test_preview_is_downscaled() -> None:
    preview = reencode_preview(png_bytes(size=(4000, 1000)))
    with Image.open(io.BytesIO(preview)) as img:
        assert max(img.size) <= 2000
