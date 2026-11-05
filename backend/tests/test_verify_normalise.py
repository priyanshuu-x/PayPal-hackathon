from datetime import date
from decimal import Decimal

import pytest

from app.verify.normalise import (
    clean_text,
    normalise_address,
    normalise_amount,
    normalise_currency,
    normalise_date,
    normalise_id,
    normalise_merchant,
    normalise_words,
)


@pytest.mark.parametrize(
    ("value", "limit", "expected"),
    [
        (None, 100, None),
        ("  hello   world ", 100, "hello world"),
        ("bad\x00\x07chars\nhere", 100, "badcharshere"),
        ("   ", 100, None),
        ("\x00\x01", 100, None),
        ("a" * 50, 10, "a" * 10),
    ],
)
def test_clean_text(value: str | None, limit: int, expected: str | None) -> None:
    assert clean_text(value, limit) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, None), ("  ab-12 #c ", "AB12C"), ("---", None), ("", None), ("5o19 0127", "5O190127")],
)
def test_normalise_id(value: str | None, expected: str | None) -> None:
    assert normalise_id(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("$", "USD"),
        ("US$", "USD"),
        ("€", "EUR"),
        ("£", "GBP"),
        ("₹", "INR"),
        ("usd", "USD"),
        (" eur ", "EUR"),
        ("dollars", None),
        ("X1Z", None),
        ("", None),
    ],
)
def test_normalise_currency(value: str | None, expected: str | None) -> None:
    assert normalise_currency(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("", None),
        ("abc", None),
        ("52.99", Decimal("52.99")),
        ("$52.99", Decimal("52.99")),
        ("USD 52.99", Decimal("52.99")),
        ("$1,234.50", Decimal("1234.50")),  # comma thousands, dot decimal
        ("1.234,50 EUR", Decimal("1234.50")),  # dot thousands, comma decimal
        ("12,50", Decimal("12.50")),  # lone comma with two digits is a decimal
        ("1,234", Decimal("1234")),  # lone comma otherwise is a thousands separator
        ("1,5,5", Decimal("155")),
        ("1.234.567", Decimal("1234567")),  # several dots are thousands separators
        ("-5", None),
        ("5-5", None),
        ("9999999999", None),
        ("0", Decimal("0")),
    ],
)
def test_normalise_amount(value: str | None, expected: Decimal | None) -> None:
    assert normalise_amount(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("2026-11-03", date(2026, 11, 3)),
        ("2026-11-03T10:00:00", date(2026, 11, 3)),
        ("2026-11-03T22:00:00-08:00", date(2026, 11, 4)),  # converted to UTC first
        ("2026-11-03T10:00:00Z", date(2026, 11, 3)),
        ("Nov 3, 2026", date(2026, 11, 3)),
        ("3 November 2026", date(2026, 11, 3)),
        ("03/11/2026", None),  # day-first or month-first is ambiguous, so it is not guessed
        ("garbage", None),
        ("", None),
    ],
)
def test_normalise_date(value: str | None, expected: date | None) -> None:
    assert normalise_date(value) == expected


def test_normalise_words_and_merchant_and_address() -> None:
    assert normalise_words(None) == ""
    assert normalise_words("Lotus & Loom, Inc.") == "lotus loom inc"
    assert normalise_merchant("Lotus & Loom Demo Store, LLC") == "lotus loom demo store"
    assert normalise_merchant(None) == ""
    assert normalise_address("12 Main St., Apt 4") == "12 main street apartment 4"
    assert normalise_address(None) == ""
