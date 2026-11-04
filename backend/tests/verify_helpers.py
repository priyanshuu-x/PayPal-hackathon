"""Builders for verify tests: a genuine claim that matches a genuine record, then small edits."""

from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.extraction.schema import ClaimedFacts, ClaimedItem
from app.verify.models import CheckOutcome, PayPalRecord, RecordItem, VerifyInput

ORDER_ID = "5O190127TN364715T"
CAPTURE_ID = "3GG279541U471931P"
STORE = "Lotus & Loom Demo Store"


def make_record(**changes: Any) -> PayPalRecord:
    base = PayPalRecord(
        order_id=ORDER_ID,
        capture_ids=(CAPTURE_ID,),
        currency="USD",
        total=Decimal("52.99"),
        created_at=datetime(2026, 11, 3, 10, 15, tzinfo=UTC),
        items=(RecordItem("Lotus Linen Tote", 2, Decimal("24.00")),),
        payer_name="Test Buyer",
        payer_email="buyer@example.com",
        shipping_address="1 Main St, San Jose, CA, 95131, US",
        tracking_status="shipped",
    )
    return replace(base, **changes)


def make_facts(**changes: Any) -> ClaimedFacts:
    data: dict[str, Any] = {
        "document_type": "receipt",
        "merchant_name": STORE,
        "order_id": ORDER_ID,
        "transaction_id": None,
        "order_date": "2026-11-03",
        "currency": "USD",
        "total_amount": "52.99",
        "items": [ClaimedItem(name="Lotus Linen Tote", quantity=2, unit_price="24.00")],
        "payer_name": "Test Buyer",
        "payer_email": "buyer@example.com",
        "shipping_address": "1 Main St, San Jose, CA, 95131, US",
        "legibility": "clear",
        "field_confidence": {"order_id": 0.95, "total_amount": 0.95, "order_date": 0.95},
    }
    data.update(changes)
    return ClaimedFacts(**data)


def make_input(
    claimed: ClaimedFacts | None = None,
    record: PayPalRecord | None | str = "default",
    **changes: Any,
) -> VerifyInput:
    return VerifyInput(
        claimed=claimed if claimed is not None else make_facts(),
        record=make_record() if record == "default" else record,  # type: ignore[arg-type]
        store_names=changes.pop("store_names", (STORE,)),
        **changes,
    )


def by_field(checks: tuple[CheckOutcome, ...]) -> dict[str, CheckOutcome]:
    return {c.field: c for c in checks}
