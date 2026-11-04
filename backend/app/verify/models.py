"""Inputs and outputs of the deterministic verification engine. No LLM, no network, no I/O."""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from app.extraction.schema import ClaimedFacts

Severity = Literal["critical", "major", "minor", "info"]
CheckResultKind = Literal["match", "mismatch", "unknown"]
Verdict = Literal["MATCHES_RECORDS", "NEEDS_REVIEW", "DOES_NOT_MATCH_RECORDS"]
# Comes from PayPal's structured dispute reason, never from buyer-written text.
ClaimType = Literal["not_received", "other"]
TrackingStatus = Literal["delivered", "shipped", "cancelled", "none"]


@dataclass(frozen=True)
class RecordItem:
    name: str
    quantity: int
    unit_price: Decimal


@dataclass(frozen=True)
class PayPalRecord:
    """The facts PayPal holds for an order. Built from API responses in verify/records.py."""

    order_id: str
    capture_ids: tuple[str, ...]
    currency: str
    total: Decimal
    created_at: datetime  # timezone-aware, UTC
    items: tuple[RecordItem, ...] = ()
    payer_name: str | None = None
    payer_email: str | None = None
    shipping_address: str | None = None
    tracking_status: TrackingStatus | None = None


@dataclass(frozen=True)
class CheckOutcome:
    field: str
    claimed: str | None
    actual: str | None
    result: CheckResultKind
    severity: Severity
    note: str


@dataclass(frozen=True)
class VerifyInput:
    claimed: ClaimedFacts
    record: PayPalRecord | None
    store_names: tuple[str, ...]
    lookup_failed: bool = False  # PayPal could not be reached: never a pass
    claim_type: ClaimType | None = None
    duplicate_file_count: int = 0  # other cases that used the same file hash
    forensic_risk: int = 0  # 0 to 15 from weak forensic signals; never changes the verdict


@dataclass(frozen=True)
class VerifyResult:
    verdict: Verdict
    risk_score: int  # 0 to 100
    checks: tuple[CheckOutcome, ...]
    reasons: tuple[str, ...]  # plain language, no accusations
