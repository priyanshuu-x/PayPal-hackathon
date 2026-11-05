from datetime import date
from decimal import Decimal

import pytest

from app.drafting.grounding import (
    MAX_NOTE_CHARS,
    GroundingFacts,
    GroundingItem,
    check_grounding,
)
from app.drafting.templates import refund_note

ORDER = "5O190127TN364715T"
CAPTURE = "3GG279541U471931P"
TRACKING = "1Z999AA10123456784"


def facts(**changes: object) -> GroundingFacts:
    base = GroundingFacts(
        order_id=ORDER,
        amount=Decimal("52.99"),
        currency="USD",
        order_date=date(2026, 11, 3),
        capture_ids=(CAPTURE,),
        tracking_number=TRACKING,
        items=(GroundingItem("Lotus Linen Tote", 2, Decimal("24.00")),),
    )
    return GroundingFacts(**{**base.__dict__, **changes})  # type: ignore[arg-type]


GROUNDED = [
    "Your refund has been issued. Thank you.",  # no values at all
    f"Your refund of 52.99 USD for order {ORDER} has been issued.",
    "Refunded $52.99.",
    "Refunded 52.990 USD.",  # same value, different spelling
    f"Order {ORDER.lower()} is refunded.",
    "Order 5O19-0127-TN364715T is refunded.",  # hyphens and case are ignored for IDs
    f"Capture {CAPTURE} was refunded.",
    f"Tracking number {TRACKING} showed the parcel as shipped.",
    "2 Lotus Linen Tote at $24.00 each.",  # quantity and unit price
    "The items came to 48.00 before shipping.",  # sum of the lines
    "Ordered on 2026-11-03.",
    "Ordered on November 3, 2026.",
    "Ordered on 3rd Nov 2026.",
    "Ordered on Nov. 3.",  # month and day only: matched against the order date's year
    "Ordered on 11/3/2026.",  # month first
    "Ordered on 3/11/2026.",  # day first
    "Ordered on 11/03/26.",  # two-digit year
    "A market stall sells forgiving, sensible gifts.",  # only contains a month or banned stem
]


@pytest.mark.parametrize("text", GROUNDED)
def test_grounded_text_passes(text: str) -> None:
    result = check_grounding(text, facts())
    assert result.grounded, result
    assert result.unsupported == () and result.issues == ()


UNSUPPORTED = [
    ("Refunded $5.99.", ["5.99"]),
    ("Refunded 52.98 USD.", ["52.98"]),
    ("Your refund arrives in 3 business days.", ["3"]),
    ("Ordered on 2026-11-04.", ["2026-11-04"]),
    ("Ordered on December 25, 2026.", ["December 25, 2026"]),
    ("Ordered on 3 November 2027.", ["3 November 2027"]),
    ("Ordered on Nov 4.", ["Nov 4"]),
    ("Ordered on 31 February 2026.", ["31 February 2026"]),  # not a real date
    ("Ordered on 4/11/2026.", ["4/11/2026"]),
    ("Ordered on 2026-02-31.", ["2026-02-31"]),
    ("Order 9ZZ00000AA111111B is refunded.", ["9ZZ00000AA111111B"]),
    ("Tracking 9400111899223197428490 was delivered.", ["9400111899223197428490"]),
    ("Code AB12CD applies.", ["AB12CD"]),
    ("Reference 12345678 noted.", ["12345678"]),
    (
        "Reference 123456 noted.",
        ["123456"],
    ),  # six digits is a number, not an ID, and is not allowed
    ("Refunded .5 USD.", [".5"]),
]


@pytest.mark.parametrize(("text", "expected"), UNSUPPORTED)
def test_values_missing_from_the_record_are_reported(text: str, expected: list[str]) -> None:
    result = check_grounding(text, facts())
    assert not result.grounded
    assert list(result.unsupported) == expected


def test_a_repeated_bad_value_is_reported_once_and_good_ones_are_not() -> None:
    result = check_grounding(f"Order {ORDER}: 9.99 now, 9.99 later, and 52.99 total.", facts())
    assert result.unsupported == ("9.99",)


@pytest.mark.parametrize(
    "text",
    [
        "This looks like fraud.",
        "We suspect a scam.",
        "Your fake receipt was noted.",
        "A forged document.",
        "Forgery is serious.",
        "Counterfeit goods.",
        "You are cheating.",
        "You are a liar.",
        "Stop lying to us.",
        "A criminal act.",
        "FRAUDULENT activity",
    ],
)
def test_accusing_words_are_never_allowed(text: str) -> None:
    result = check_grounding(text, facts())
    assert not result.grounded and result.issues and result.unsupported == ()


def test_length_limit() -> None:
    assert check_grounding("a" * MAX_NOTE_CHARS, facts()).grounded
    too_long = check_grounding("a" * (MAX_NOTE_CHARS + 1), facts())
    assert not too_long.grounded and "longer than" in too_long.issues[0]


def test_facts_without_items_or_tracking_allow_only_what_remains() -> None:
    bare = facts(items=(), tracking_number=None, capture_ids=())
    assert check_grounding("Refunded 52.99 USD for " + ORDER, bare).grounded
    assert not check_grounding("2 items", bare).grounded  # no item list, so no quantity to cite
    assert not check_grounding(f"Capture {CAPTURE}", bare).grounded
    assert not check_grounding(f"Tracking {TRACKING}", bare).grounded


def test_amounts_with_thousands_separators() -> None:
    big = facts(amount=Decimal("1234.50"), items=())
    assert check_grounding("Refunded $1,234.50.", big).grounded
    assert not check_grounding("Refunded $1,234.60.", big).grounded


def test_the_template_note_is_grounded_and_within_the_limit() -> None:
    for item_count in (0, 1, 3):
        items = tuple(GroundingItem(f"Item {i}", i + 1, Decimal("1.50")) for i in range(item_count))
        note = refund_note(facts(items=items), "Lotus & Loom Demo Store")
        result = check_grounding(note, facts(items=items))
        assert result.grounded, (note, result)
        assert len(note) <= MAX_NOTE_CHARS
    assert "52.99 USD" in refund_note(facts(), "Shop") and ORDER in refund_note(facts(), "Shop")
