"""Receipt images for the eval set, for the fictional "Lotus & Loom Demo Store" only.

Three layouts (a paper receipt, an order-confirmation email, a dark app screenshot) drawn with
Pillow, plus degradations (blur, rotation, crop, JPEG noise). There are no real retailer
names, logos or templates, and nothing here takes arbitrary input: it renders `Printed` data
built by `generate_cases.py` from our own sandbox orders (plan.md §9.1, §10.3).
"""

from __future__ import annotations

import io
import random
from datetime import date
from decimal import Decimal
from typing import Any

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from dataset import Printed

LAYOUTS = ("paper", "email", "app")
DEGRADATIONS = ("blur", "rotate", "crop", "jpeg")
DATE_STYLES = ("iso", "us", "eu")


def _font(size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    return ImageFont.load_default(size=size)


def format_date(iso: str, style: str) -> str:
    d = date.fromisoformat(iso)
    if style == "us":
        return d.strftime("%b %d, %Y")  # Nov 03, 2026
    if style == "eu":
        return d.strftime("%d %B %Y")  # 03 November 2026
    return d.isoformat()


def _money(value: str, currency: str) -> str:
    return f"${Decimal(value):,.2f}" if currency == "USD" else f"{Decimal(value):,.2f} {currency}"


def _lines(p: Printed, date_style: str) -> list[tuple[str, str, str]]:
    """(kind, left, right) rows shared by all layouts."""
    rows: list[tuple[str, str, str]] = [
        ("title", p.merchant, ""),
        ("muted", "Order confirmation", ""),
        ("gap", "", ""),
        ("row", "Order ID", p.order_id),
        ("row", "Date", format_date(p.order_date, date_style)),
    ]
    if p.payer_name:
        rows.append(("row", "Customer", p.payer_name))
    rows.append(("rule", "", ""))
    for item in p.items:
        line_total = str(Decimal(item.unit_price) * item.quantity)
        rows.append(("row", f"{item.quantity} x {item.name}", _money(line_total, p.currency)))
    rows.append(("rule", "", ""))
    rows.append(("row", "Shipping", _money(p.shipping, p.currency)))
    rows.append(("bold", f"Total ({p.currency})", _money(p.total, p.currency)))
    rows.append(("muted", "Paid with PayPal", ""))
    if p.ship_to:
        rows.append(("gap", "", ""))
        rows.append(("muted", "Ship to", ""))
        rows.extend(("small", part.strip(), "") for part in p.ship_to.split(",")[:4])
    if p.injection:
        rows.append(("gap", "", ""))
        rows.extend(("small", chunk, "") for chunk in _wrap(p.injection, 48))
    rows.append(("gap", "", ""))
    rows.append(("muted", "Thank you for shopping with us.", ""))
    return rows


def _wrap(text: str, width: int) -> list[str]:
    words, lines, current = text.split(), [], ""
    for word in words:
        if len(current) + len(word) + 1 > width and current:
            lines.append(current)
            current = word
        else:
            current = f"{current} {word}".strip()
    return [*lines, current] if current else lines


def render(p: Printed, layout: str, date_style: str) -> Image.Image:
    rows = _lines(p, date_style)
    style: dict[str, Any] = {
        "paper": {"w": 560, "bg": "#fbfaf7", "fg": "#1d1d1f", "muted": "#6b6b6b", "pad": 36},
        "email": {"w": 820, "bg": "#ffffff", "fg": "#14213d", "muted": "#5c677d", "pad": 56},
        "app": {"w": 640, "bg": "#16181d", "fg": "#f2f2f2", "muted": "#a0a4ad", "pad": 40},
    }[layout]
    sizes = {"title": 30, "muted": 18, "row": 20, "bold": 22, "small": 16}
    height = style["pad"] * 2 + sum(
        {"gap": 14, "rule": 18}.get(kind, sizes.get(kind, 20) + 12) for kind, _, _ in rows
    )
    img = Image.new("RGB", (style["w"], height), style["bg"])
    draw = ImageDraw.Draw(img)
    if layout == "email":
        draw.rectangle([0, 0, style["w"], 10], fill="#2b6cb0")
    y = style["pad"]
    left, right = style["pad"], style["w"] - style["pad"]
    for kind, text, value in rows:
        if kind == "gap":
            y += 14
            continue
        if kind == "rule":
            draw.line([left, y + 8, right, y + 8], fill=style["muted"], width=1)
            y += 18
            continue
        font = _font(sizes[kind])
        colour = style["muted"] if kind in ("muted", "small") else style["fg"]
        draw.text((left, y), text, font=font, fill=colour)
        if value:
            width = draw.textlength(value, font=font)
            draw.text((right - width, y), value, font=font, fill=colour)
        y += sizes[kind] + 12
    return img


def degrade(img: Image.Image, kind: str, rng: random.Random) -> Image.Image:
    if kind == "blur":
        return img.filter(ImageFilter.GaussianBlur(radius=rng.uniform(0.8, 1.6)))
    if kind == "rotate":
        return img.rotate(rng.uniform(-6, 6), expand=True, fillcolor="#d9d9d9")
    if kind == "crop":
        w, h = img.size
        m = int(min(w, h) * 0.02)  # trim margins only, never the text
        return img.crop((m, m, w - m, h - m))
    if kind == "jpeg":
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=rng.randint(18, 30))
        buf.seek(0)
        return Image.open(buf).convert("RGB")
    raise ValueError(kind)


def to_png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def to_jpeg(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()
