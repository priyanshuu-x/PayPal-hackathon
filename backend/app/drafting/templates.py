"""Fixed-wording notes. Built only from record values, so they are grounded by construction and
are used when the model cannot produce a grounded draft in three tries."""

from app.drafting.grounding import GroundingFacts

TEMPLATE_VERSION = "TEMPLATE_V1"


def dispute_note(facts: GroundingFacts, tracking_status: str | None) -> str:
    """The evidence note for a dispute, from record values only."""
    opening = (
        f"Order {facts.order_id} for {facts.amount} {facts.currency} "
        f"was placed on {facts.order_date.isoformat()}."
    )
    if facts.tracking_number:
        status = (
            f" and PayPal's shipment record shows the status as {tracking_status}"
            if tracking_status
            else ""
        )
        return (
            f"{opening} It shipped with tracking number {facts.tracking_number}{status}. "
            "We ask PayPal to review this evidence."
        )
    return (
        f"{opening} We have no shipment tracking on file for this order. "
        "We ask PayPal to review the case."
    )


def refund_note(facts: GroundingFacts, store_name: str) -> str:
    return (
        f"Your refund of {facts.amount} {facts.currency} for order {facts.order_id} "
        f"has been issued. Thank you for shopping with {store_name}."
    )
