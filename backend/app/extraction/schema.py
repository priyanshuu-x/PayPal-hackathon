"""What a document claims. LLM output is untrusted: it is parsed into this schema, and a parse
failure becomes NEEDS_REVIEW (CLAUDE.md rule 1).

Models sometimes return numbers where we ask for strings (`52.99` instead of `"52.99"`), so
string fields accept numbers and convert them. Anything else that does not fit fails validation.
"""

from decimal import Decimal
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

DocumentType = Literal["receipt", "order_confirmation", "screenshot", "product_photo", "other"]
Legibility = Literal["clear", "partial", "poor"]
_DOCUMENT_TYPES = {"receipt", "order_confirmation", "screenshot", "product_photo", "other"}


def _to_str(value: Any) -> Any:
    if isinstance(value, bool):
        return value  # left alone so validation rejects it
    if isinstance(value, int | float | Decimal):
        return str(value)
    return value


def _to_int(value: Any) -> Any:
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return value


def _document_type(value: Any) -> Any:
    text = str(value).strip().lower().replace(" ", "_") if value is not None else "other"
    return text if text in _DOCUMENT_TYPES else "other"


def _legibility(value: Any) -> Any:
    return str(value).strip().lower() if isinstance(value, str) else value


def _confidence(value: Any) -> Any:
    """Accept 0 to 1, and treat 2 to 100 as a percentage."""
    if isinstance(value, int | float) and not isinstance(value, bool) and 1 < value <= 100:
        return value / 100
    return value


LooseStr = Annotated[str | None, BeforeValidator(_to_str)]
LooseInt = Annotated[int | None, BeforeValidator(_to_int)]
Confidence = Annotated[float, BeforeValidator(_confidence), Field(ge=0.0, le=1.0)]


class ClaimedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: Annotated[str, BeforeValidator(_to_str)]
    quantity: LooseInt = None
    unit_price: LooseStr = None


class ClaimedFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_type: Annotated[DocumentType, BeforeValidator(_document_type)] = "other"
    merchant_name: LooseStr = None
    order_id: LooseStr = None
    transaction_id: LooseStr = None
    order_date: LooseStr = None  # YYYY-MM-DD
    currency: LooseStr = None
    total_amount: LooseStr = None  # decimal string
    items: list[ClaimedItem] = Field(default_factory=list)
    payer_name: LooseStr = None
    payer_email: LooseStr = None
    shipping_address: LooseStr = None
    visible_damage_description: LooseStr = None
    embedded_instructions_detected: bool = False
    # Kept for the merchant's audit view only. It never goes into a prompt or a check table.
    embedded_instructions_text: LooseStr = None
    legibility: Annotated[Legibility, BeforeValidator(_legibility)] = "clear"
    field_confidence: dict[str, Confidence] = Field(default_factory=dict)
