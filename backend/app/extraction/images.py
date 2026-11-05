"""Turn an uploaded file into model-ready images, and render PDF pages.

Inputs were already validated by security/uploads.py (type, size, pixels, page cap).
"""

import io

import pypdfium2 as pdfium
from PIL import Image, ImageOps

MAX_MODEL_PAGES = 3
MODEL_MAX_SIDE = 1600
PDF_RENDER_SCALE = 2.0
MAX_RENDER_PIXELS = 12_000_000


class ImageError(Exception):
    """The file could not be turned into images."""


def _to_jpeg(image: Image.Image) -> bytes:
    flat = Image.new("RGB", image.size, "white")
    rgba = image.convert("RGBA")
    flat.paste(rgba, mask=rgba.getchannel("A"))
    flat.thumbnail((MODEL_MAX_SIDE, MODEL_MAX_SIDE))
    out = io.BytesIO()
    flat.save(out, format="JPEG", quality=85)
    return out.getvalue()


def render_pdf_pages(data: bytes, max_pages: int = MAX_MODEL_PAGES) -> list[Image.Image]:
    """Render the first pages of a PDF to RGB images."""
    try:
        pdf = pdfium.PdfDocument(data)
        try:
            pages = []
            for index in range(min(len(pdf), max_pages)):
                page = pdf[index]
                width, height = page.get_size()
                scale = PDF_RENDER_SCALE
                if width * height * scale * scale > MAX_RENDER_PIXELS:
                    scale = (MAX_RENDER_PIXELS / (width * height)) ** 0.5
                pages.append(page.render(scale=scale).to_pil().convert("RGB"))
            return pages
        finally:
            pdf.close()
    except Exception:  # pdfium raises a few types on malformed files
        raise ImageError("The PDF could not be rendered.") from None


def images_for_model(data: bytes, mime: str) -> list[bytes]:
    """JPEG bytes for the model: the image itself, or up to 3 rendered PDF pages."""
    try:
        if mime == "application/pdf":
            pages = render_pdf_pages(data)
            if not pages:
                raise ImageError("The PDF has no pages.")
            return [_to_jpeg(page) for page in pages]
        with Image.open(io.BytesIO(data)) as img:
            img.load()
            return [_to_jpeg(ImageOps.exif_transpose(img) or img)]
    except ImageError:
        raise
    except (OSError, ValueError, SyntaxError):
        raise ImageError("The image could not be read.") from None


def pdf_preview_png(data: bytes) -> bytes:
    """First page of a PDF as a metadata-free PNG, for the merchant's evidence preview."""
    pages = render_pdf_pages(data, max_pages=1)
    if not pages:
        raise ImageError("The PDF has no pages.")
    pages[0].thumbnail((2000, 2000))
    out = io.BytesIO()
    pages[0].save(out, format="PNG")
    return out.getvalue()
