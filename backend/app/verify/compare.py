"""One comparator per field. Each returns a CheckOutcome with its BASE severity; confidence caps
and the verdict are applied in rules.py. Pure functions over plain data."""

from decimal import Decimal

from rapidfuzz import fuzz
from rapidfuzz.distance import Levenshtein

from app.extraction.schema import ClaimedFacts
from app.verify.models import CheckOutcome, ClaimType, PayPalRecord
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

AMOUNT_TOLERANCE = Decimal("0.01")
DATE_WINDOW_DAYS = 1
ITEM_MATCH_SCORE = 85
MERCHANT_MATCH_SCORE = 85
PAYER_MATCH_SCORE = 80
ADDRESS_MATCH_SCORE = 80


def _unknown(field: str, claimed: str | None, actual: str | None, note: str) -> CheckOutcome:
    return CheckOutcome(field, claimed, actual, "unknown", "info", note)


def check_order(claimed: ClaimedFacts, record: PayPalRecord | None) -> CheckOutcome:
    order_id = normalise_id(claimed.order_id)
    transaction_id = normalise_id(claimed.transaction_id)
    shown = clean_text(claimed.order_id or claimed.transaction_id)
    if record is None:
        return CheckOutcome(
            "order", shown, None, "mismatch", "critical",
            "No order with this ID was found in PayPal for this merchant.",
        )  # fmt: skip
    actual = record.order_id
    if order_id is None and transaction_id is None:
        return _unknown("order", None, actual, "No order or transaction ID could be read.")

    known = {normalise_id(record.order_id), *(normalise_id(c) for c in record.capture_ids)}
    known.discard(None)
    worst_distance: int | None = None
    for identifier in (order_id, transaction_id):
        if identifier is None or identifier in known:
            continue
        distance = min(Levenshtein.distance(identifier, k) for k in known if k is not None)
        worst_distance = distance if worst_distance is None else max(worst_distance, distance)

    if worst_distance is None:
        return CheckOutcome("order", shown, actual, "match", "info", "Order ID matches PayPal.")
    if worst_distance == 1:
        return CheckOutcome(
            "order", shown, actual, "mismatch", "major",
            "The order ID differs from PayPal's by one character. It may be a misread.",
        )  # fmt: skip
    return CheckOutcome(
        "order", shown, actual, "mismatch", "critical",
        "The order or transaction ID does not match this order in PayPal.",
    )  # fmt: skip


def check_amount(claimed: ClaimedFacts, record: PayPalRecord) -> CheckOutcome:
    shown = clean_text(claimed.total_amount)
    actual = f"{record.total} {record.currency}"
    amount = normalise_amount(claimed.total_amount)
    if amount is None:
        return _unknown("amount", shown, actual, "No total amount could be read.")
    currency = normalise_currency(claimed.currency)
    if currency is None:
        return _unknown(
            "amount", shown, actual, "The currency is not shown, so the total cannot be compared."
        )
    claimed_text = f"{amount} {currency}"
    if currency != record.currency:
        return CheckOutcome(
            "amount", claimed_text, actual, "mismatch", "major",
            "The document uses a different currency than the order.",
        )  # fmt: skip
    if abs(amount - record.total) <= AMOUNT_TOLERANCE:
        return CheckOutcome(
            "amount", claimed_text, actual, "match", "info", "Total matches PayPal."
        )
    return CheckOutcome(
        "amount", claimed_text, actual, "mismatch", "critical",
        "The total on the document differs from the amount PayPal charged.",
    )  # fmt: skip


def check_date(claimed: ClaimedFacts, record: PayPalRecord) -> CheckOutcome:
    shown = clean_text(claimed.order_date)
    actual = record.created_at.date().isoformat()
    parsed = normalise_date(claimed.order_date)
    if parsed is None:
        return _unknown("date", shown, actual, "No order date could be read.")
    gap = abs((parsed - record.created_at.date()).days)
    if gap <= DATE_WINDOW_DAYS:
        return CheckOutcome(
            "date", parsed.isoformat(), actual, "match", "info", "Date matches PayPal."
        )
    return CheckOutcome(
        "date", parsed.isoformat(), actual, "mismatch", "major",
        f"The date on the document is {gap} days from the order date in PayPal.",
    )  # fmt: skip


def check_items(claimed: ClaimedFacts, record: PayPalRecord) -> CheckOutcome:
    shown = "; ".join(filter(None, (clean_text(i.name, 60) for i in claimed.items)))[:200] or None
    actual = "; ".join(i.name for i in record.items)[:200] or None
    if not claimed.items:
        return _unknown("items", None, actual, "No line items could be read.")
    if not record.items:
        return _unknown("items", shown, None, "PayPal has no line items for this order.")

    problems = 0
    for item in claimed.items:
        name = normalise_words(item.name)
        best = max(record.items, key=lambda r: fuzz.token_sort_ratio(name, normalise_words(r.name)))
        if fuzz.token_sort_ratio(name, normalise_words(best.name)) < ITEM_MATCH_SCORE:
            problems += 1
            continue
        if item.quantity is not None and item.quantity != best.quantity:
            problems += 1
            continue
        price = normalise_amount(item.unit_price)
        # Receipts print either the unit price or the line total (quantity x unit price), and a
        # model transcribing the price column cannot tell which. Either is correct; anything
        # else is a mismatch. (The order total is checked separately and stays exact.)
        accepted = (best.unit_price, best.unit_price * best.quantity)
        if price is not None and all(abs(price - ok) > AMOUNT_TOLERANCE for ok in accepted):
            problems += 1
    if problems:
        return CheckOutcome(
            "items", shown, actual, "mismatch", "major",
            f"{problems} item(s) on the document do not match the order in PayPal.",
        )  # fmt: skip
    return CheckOutcome("items", shown, actual, "match", "info", "Items match PayPal.")


def check_merchant(claimed: ClaimedFacts, store_names: tuple[str, ...]) -> CheckOutcome:
    shown = clean_text(claimed.merchant_name)
    actual = "; ".join(store_names) or None
    name = normalise_merchant(claimed.merchant_name)
    if not name:
        return _unknown("merchant", shown, actual, "No merchant name could be read.")
    if not store_names:
        return _unknown("merchant", shown, actual, "No store names are configured to compare with.")
    best = max(fuzz.token_sort_ratio(name, normalise_merchant(s)) for s in store_names)
    if best >= MERCHANT_MATCH_SCORE:
        return CheckOutcome(
            "merchant", shown, actual, "match", "info", "Merchant name matches this store."
        )
    return CheckOutcome(
        "merchant", shown, actual, "mismatch", "major",
        "The merchant name on the document is not this store.",
    )  # fmt: skip


def check_payer(claimed: ClaimedFacts, record: PayPalRecord) -> CheckOutcome:
    shown = clean_text(claimed.payer_name or claimed.payer_email)
    actual = record.payer_name or record.payer_email
    differences = 0
    compared = 0
    if claimed.payer_email and record.payer_email:
        compared += 1
        differences += claimed.payer_email.strip().lower() != record.payer_email.strip().lower()
    if claimed.payer_name and record.payer_name:
        compared += 1
        score = fuzz.token_sort_ratio(
            normalise_words(claimed.payer_name), normalise_words(record.payer_name)
        )
        differences += score < PAYER_MATCH_SCORE
    if not compared:
        return _unknown("payer", shown, actual, "No payer details to compare.")
    if differences:
        return CheckOutcome(
            "payer", shown, actual, "mismatch", "minor",
            "The payer name or email differs from PayPal's record.",
        )  # fmt: skip
    return CheckOutcome("payer", shown, actual, "match", "info", "Payer details match PayPal.")


def check_shipping(claimed: ClaimedFacts, record: PayPalRecord) -> CheckOutcome:
    shown = clean_text(claimed.shipping_address, 200)
    actual = record.shipping_address
    claimed_address = normalise_address(claimed.shipping_address)
    record_address = normalise_address(record.shipping_address)
    if not claimed_address or not record_address:
        return _unknown("shipping", shown, actual, "No shipping address to compare.")
    if fuzz.token_set_ratio(claimed_address, record_address) >= ADDRESS_MATCH_SCORE:
        return CheckOutcome(
            "shipping", shown, actual, "match", "info", "Shipping address matches PayPal."
        )
    return CheckOutcome(
        "shipping", shown, actual, "mismatch", "minor",
        "The shipping address differs from PayPal's record.",
    )  # fmt: skip


def check_delivery(claim_type: ClaimType | None, record: PayPalRecord) -> CheckOutcome:
    status = record.tracking_status
    if claim_type != "not_received":
        return _unknown("delivery", None, status, "No delivery claim was made.")
    if status is None or status == "none":
        return _unknown(
            "delivery", "not received", status, "PayPal has no tracking for this order."
        )
    if status == "delivered":
        return CheckOutcome(
            "delivery", "not received", status, "mismatch", "major",
            "The buyer says the item did not arrive, but PayPal tracking shows it as delivered.",
        )  # fmt: skip
    return CheckOutcome(
        "delivery", "not received", status, "match", "info",
        "PayPal tracking does not show the order as delivered.",
    )  # fmt: skip


def check_embedded_instructions(claimed: ClaimedFacts) -> CheckOutcome:
    if claimed.embedded_instructions_detected:
        # The text itself is deliberately not copied here: it is hostile input.
        return CheckOutcome(
            "embedded_instructions", "present", "none expected", "mismatch", "critical",
            "The document contains text addressed to an AI or a reviewer. It was ignored.",
        )  # fmt: skip
    return CheckOutcome(
        "embedded_instructions",
        "none",
        "none expected",
        "match",
        "info",
        "No hidden instructions found.",
    )


def check_reused_file(duplicate_file_count: int) -> CheckOutcome:
    if duplicate_file_count > 0:
        return CheckOutcome(
            "reused_file", "same file", f"also used in {duplicate_file_count} other case(s)",
            "mismatch", "major", "This exact file was already submitted in another case.",
        )  # fmt: skip
    return CheckOutcome(
        "reused_file",
        "new file",
        "not seen before",
        "match",
        "info",
        "File has not been used before.",
    )
