"""Fixed-wording notes. Built only from record values, so they are grounded by construction and
are used when the model cannot produce a grounded draft in three tries."""

from app.drafting.grounding import GroundingFacts

TEMPLATE_VERSION = "TEMPLATE_V1"


def refund_note(facts: GroundingFacts, store_name: str) -> str:
    return (
        f"Your refund of {facts.amount} {facts.currency} for order {facts.order_id} "
        f"has been issued. Thank you for shopping with {store_name}."
    )
