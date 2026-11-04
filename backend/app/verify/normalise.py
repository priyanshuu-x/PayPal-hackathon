"""Turn messy extracted strings into comparable values. Unparseable input becomes None."""

import re
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

_CURRENCY_SYMBOLS = {"$": "USD", "US$": "USD", "€": "EUR", "£": "GBP", "₹": "INR"}
_MAX_AMOUNT = Decimal("1000000000")
_DATE_FORMATS = ("%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%b %d %Y", "%B %d %Y")
_COMPANY_SUFFIXES = frozenset({"inc", "llc", "ltd", "co", "corp", "company"})
_ADDRESS_WORDS = {
    "st": "street",
    "rd": "road",
    "ave": "avenue",
    "blvd": "boulevard",
    "dr": "drive",
    "ln": "lane",
    "apt": "apartment",
}


def clean_text(value: str | None, limit: int = 100) -> str | None:
    """Printable characters only, collapsed whitespace, length-capped. Used for anything that
    comes from a document before it is shown in a table."""
    if value is None:
        return None
    printable = "".join(ch for ch in value if ch.isprintable())
    collapsed = " ".join(printable.split())[:limit]
    return collapsed or None


def normalise_id(value: str | None) -> str | None:
    if value is None:
        return None
    return re.sub(r"[^A-Za-z0-9]", "", value).upper() or None


def normalise_currency(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    if text in _CURRENCY_SYMBOLS:
        return _CURRENCY_SYMBOLS[text]
    if len(text) == 3 and text.isalpha():
        return text.upper()
    return None


def normalise_amount(value: str | None) -> Decimal | None:
    """'$1,234.50', '1.234,50 EUR', '52.99' -> Decimal. Negative or absurd values -> None."""
    if value is None:
        return None
    text = re.sub(r"[^0-9.,-]", "", value)
    if not text or text.startswith("-"):
        return None
    has_comma, has_dot = "," in text, "." in text
    if has_comma and has_dot:
        decimal_sep = "," if text.rfind(",") > text.rfind(".") else "."
        thousands_sep = "." if decimal_sep == "," else ","
        text = text.replace(thousands_sep, "").replace(decimal_sep, ".")
    elif has_comma:
        if text.count(",") == 1 and re.search(r",\d{2}$", text):
            text = text.replace(",", ".")
        else:
            text = text.replace(",", "")
    elif text.count(".") > 1:
        text = text.replace(".", "")
    try:
        amount = Decimal(text)
    except InvalidOperation:
        return None
    if amount < 0 or amount >= _MAX_AMOUNT:
        return None
    return amount


def normalise_date(value: str | None) -> date | None:
    """ISO dates (with or without a time) and 'Nov 3, 2026' style dates. Slash dates are
    ambiguous (day first or month first), so they are not guessed."""
    if value is None:
        return None
    text = value.strip()
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        pass
    else:
        return (parsed.astimezone(UTC) if parsed.tzinfo else parsed).date()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def normalise_words(value: str | None) -> str:
    """Lower-case words separated by single spaces, punctuation removed."""
    if value is None:
        return ""
    return " ".join(re.sub(r"[^a-z0-9]+", " ", value.lower()).split())


def normalise_merchant(value: str | None) -> str:
    words = [w for w in normalise_words(value).split() if w not in _COMPANY_SUFFIXES]
    return " ".join(words)


def normalise_address(value: str | None) -> str:
    return " ".join(_ADDRESS_WORDS.get(w, w) for w in normalise_words(value).split())
