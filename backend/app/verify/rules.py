"""Run every check, apply confidence caps, and decide the verdict. Deterministic and LLM-free.

Verdict rules (plan section 7.4):
- DOES_NOT_MATCH_RECORDS: at least one critical mismatch.
- NEEDS_REVIEW: any major mismatch, an unreadable document, a required field that could not be
  read, a low-confidence read of a key field, or PayPal records that could not be fetched.
- MATCHES_RECORDS: otherwise.

A critical mismatch on a key field that was read with low confidence is capped to major, so a
shaky read leads to a human check and never to a "does not match" verdict.
"""

from dataclasses import replace

from app.extraction.schema import ClaimedFacts
from app.verify import compare
from app.verify.models import CheckOutcome, Verdict, VerifyInput

LOW_CONFIDENCE = 0.6
REQUIRED_FIELDS = ("order", "amount", "date")
TEXT_DOCUMENT_TYPES = frozenset({"receipt", "order_confirmation", "screenshot"})
_SEVERITY_ORDER = {"critical": 0, "major": 1, "minor": 2, "info": 3}


def _confidence(claimed: ClaimedFacts, *keys: str) -> float:
    """Best confidence among the given keys that the extractor reported; missing means low."""
    values = [claimed.field_confidence[k] for k in keys if k in claimed.field_confidence]
    return max(values, default=0.0)


def _cap(outcome: CheckOutcome, confidence: float) -> CheckOutcome:
    if (
        outcome.result == "mismatch"
        and outcome.severity == "critical"
        and confidence < LOW_CONFIDENCE
    ):
        return replace(
            outcome,
            severity="major",
            note=f"{outcome.note} It was read with low confidence, so a person should check it.",
        )
    return outcome


def _pseudo(field: str, note: str) -> CheckOutcome:
    return CheckOutcome(field, None, None, "mismatch", "major", note)


def evaluate(inp: VerifyInput) -> tuple[CheckOutcome, ...]:
    claimed, record = inp.claimed, inp.record

    if inp.lookup_failed:
        # Nothing can be compared without PayPal's side, so the result is always a review.
        return (
            _pseudo("records", "PayPal's records could not be fetched. Try again."),
            compare.check_embedded_instructions(claimed),
            compare.check_reused_file(inp.duplicate_file_count),
        )

    order = compare.check_order(claimed, record)
    outcomes: list[CheckOutcome] = [_cap(order, _confidence(claimed, "order_id", "transaction_id"))]
    if record is not None:
        amount = compare.check_amount(claimed, record)
        outcomes += [
            _cap(amount, _confidence(claimed, "total_amount")),
            compare.check_date(claimed, record),
            compare.check_items(claimed, record),
            compare.check_merchant(claimed, inp.store_names),
            compare.check_payer(claimed, record),
            compare.check_shipping(claimed, record),
            compare.check_delivery(inp.claim_type, record),
        ]
    outcomes.append(compare.check_embedded_instructions(claimed))
    outcomes.append(compare.check_reused_file(inp.duplicate_file_count))
    outcomes += _read_quality_checks(claimed, outcomes)
    return tuple(outcomes)


def _read_quality_checks(claimed: ClaimedFacts, outcomes: list[CheckOutcome]) -> list[CheckOutcome]:
    extra: list[CheckOutcome] = []

    if claimed.legibility == "poor":
        extra.append(_pseudo("legibility", "The document is too hard to read to verify reliably."))

    if claimed.document_type in TEXT_DOCUMENT_TYPES:
        unknown = [
            o.field for o in outcomes if o.field in REQUIRED_FIELDS and o.result == "unknown"
        ]
        if unknown:
            extra.append(_pseudo("required_fields", "Could not read: " + ", ".join(unknown) + "."))
        low = _low_confidence_matches(claimed, outcomes)
        if low:
            extra.append(
                _pseudo("read_confidence", "Low confidence reading: " + ", ".join(low) + ".")
            )
    else:
        extra.append(
            _pseudo("document", "This document shows no order details that can be verified.")
        )
    return extra


def _low_confidence_matches(claimed: ClaimedFacts, outcomes: list[CheckOutcome]) -> list[str]:
    keys = {
        "order": ("order_id", "transaction_id"),
        "amount": ("total_amount",),
        "date": ("order_date",),
    }
    return [
        o.field
        for o in outcomes
        if o.field in keys
        and o.result == "match"
        and _confidence(claimed, *keys[o.field]) < LOW_CONFIDENCE
    ]


def decide_verdict(outcomes: tuple[CheckOutcome, ...]) -> Verdict:
    mismatches = [o for o in outcomes if o.result == "mismatch"]
    if any(o.severity == "critical" for o in mismatches):
        return "DOES_NOT_MATCH_RECORDS"
    if any(o.severity == "major" for o in mismatches):
        return "NEEDS_REVIEW"
    return "MATCHES_RECORDS"


def build_reasons(outcomes: tuple[CheckOutcome, ...]) -> tuple[str, ...]:
    """Plain-language reasons, worst first. Minor ones are listed but never change the verdict."""
    mismatches = sorted(
        (o for o in outcomes if o.result == "mismatch"), key=lambda o: _SEVERITY_ORDER[o.severity]
    )
    if not mismatches:
        return ("Every field that could be checked matches PayPal's records.",)
    return tuple(o.note for o in mismatches)
