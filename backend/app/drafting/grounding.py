"""Deterministic grounding check for outgoing text (CLAUDE.md rule 6).

Every number, date, ID and tracking number in a draft must exist in the PayPal record, and
customer-facing text never uses accusing words (rule 11). No LLM is involved and the check is
never relaxed to make a test pass.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_NOTE_CHARS = 240  # PayPal limits a refund note to 255 characters

BANNED_TERMS = (
    "fraud", "scam", "fake", "forged", "forgery", "counterfeit",
    "cheat", "liar", "lying", "criminal",
)  # fmt: skip

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}  # fmt: skip
_MONTH = r"(?P<{n}>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"  # noqa: E501
_ORD = r"(?:st|nd|rd|th)?"

_DATE_PATTERNS = (
    re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{1,2})-(?P<d>\d{1,2})\b"),
    re.compile(
        rf"\b{_MONTH.format(n='mon')}\.? (?P<d>\d{{1,2}}){_ORD},? (?P<y>\d{{4}})\b", re.IGNORECASE
    ),
    re.compile(
        rf"\b(?P<d>\d{{1,2}}){_ORD} {_MONTH.format(n='mon')}\.?,? (?P<y>\d{{4}})\b", re.IGNORECASE
    ),
    re.compile(rf"\b{_MONTH.format(n='mon')}\.? (?P<d>\d{{1,2}}){_ORD}\b", re.IGNORECASE),
    re.compile(r"\b(?P<a>\d{1,2})/(?P<b>\d{1,2})/(?P<y>\d{2,4})\b"),
)
_ID_TOKEN = re.compile(r"\b[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*\b")
_NUMBER = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?|\.\d+")


@dataclass(frozen=True)
class GroundingItem:
    name: str
    quantity: int
    unit_price: Decimal


@dataclass(frozen=True)
class GroundingFacts:
    """The only values an outgoing note may contain, all taken from PayPal's record."""

    order_id: str
    amount: Decimal
    currency: str
    order_date: date
    capture_ids: tuple[str, ...] = ()
    tracking_number: str | None = None
    items: tuple[GroundingItem, ...] = ()


@dataclass(frozen=True)
class GroundingResult:
    grounded: bool
    unsupported: tuple[str, ...]  # numbers, dates and IDs that are not in the record
    issues: tuple[str, ...]  # other problems: length, banned wording


def _normalise_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", value).upper()


def _allowed_ids(facts: GroundingFacts) -> set[str]:
    ids = {facts.order_id, *facts.capture_ids}
    if facts.tracking_number:
        ids.add(facts.tracking_number)
    return {_normalise_id(i) for i in ids if i}


def _allowed_numbers(facts: GroundingFacts) -> set[Decimal]:
    numbers = {facts.amount}
    items_total = Decimal(0)
    for item in facts.items:
        line = item.unit_price * item.quantity
        items_total += line
        numbers |= {Decimal(item.quantity), item.unit_price, line}
    if facts.items:
        numbers.add(items_total)
    return numbers


def _looks_like_id(token: str) -> bool:
    plain = _normalise_id(token)
    has_digit = any(c.isdigit() for c in plain)
    has_alpha = any(c.isalpha() for c in plain)
    return len(plain) >= 6 and has_digit and (has_alpha or len(plain) >= 8)


def _date_from_match(match: re.Match[str]) -> list[tuple[int | None, int, int]]:
    """Possible (year, month, day) readings of a matched date; year None when not written."""
    groups = match.groupdict()
    year: int | None = None
    if groups.get("y"):
        year = int(groups["y"])
        if year < 100:
            year += 2000
    if "a" in groups and groups["a"] is not None:  # 03/11/2026 could be either order
        first, second = int(groups["a"]), int(groups["b"])
        return [(year, first, second), (year, second, first)]
    month = groups["m"] if groups.get("m") else str(_MONTHS[groups["mon"][:3].lower()])
    return [(year, int(month), int(groups["d"]))]


def _date_ok(reading: tuple[int | None, int, int], allowed: date) -> bool:
    year, month, day = reading
    try:
        parsed = date(year if year is not None else allowed.year, month, day)
    except ValueError:  # for example 31 February
        return False
    return parsed == allowed


def check_grounding(text: str, facts: GroundingFacts) -> GroundingResult:
    unsupported: list[str] = []
    issues: list[str] = []

    if len(text) > MAX_NOTE_CHARS:
        issues.append(f"longer than {MAX_NOTE_CHARS} characters")
    lowered = text.lower()
    for term in BANNED_TERMS:
        if re.search(rf"\b{term}", lowered):
            issues.append(f"uses the word '{term}'")

    remaining = text
    for pattern in _DATE_PATTERNS:
        for match in pattern.finditer(remaining):
            if not any(_date_ok(r, facts.order_date) for r in _date_from_match(match)):
                unsupported.append(match.group(0))
        remaining = pattern.sub(" ", remaining)

    allowed_ids = _allowed_ids(facts)
    for token in _ID_TOKEN.findall(remaining):
        if _looks_like_id(token) and _normalise_id(token) not in allowed_ids:
            unsupported.append(token)
    remaining = _ID_TOKEN.sub(
        lambda m: " " if _looks_like_id(m.group(0)) else m.group(0), remaining
    )

    allowed_numbers = _allowed_numbers(facts)
    for token in _NUMBER.findall(remaining):
        try:
            value = Decimal(token.replace(",", ""))
        except InvalidOperation:  # pragma: no cover  (the pattern only matches valid numbers)
            unsupported.append(token)
            continue
        if value not in allowed_numbers:
            unsupported.append(token)

    unique = tuple(dict.fromkeys(unsupported))
    return GroundingResult(not unique and not issues, unique, tuple(issues))
