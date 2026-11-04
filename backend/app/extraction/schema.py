"""What a document claims. LLM output is untrusted: it is parsed into this schema, and a parse
failure becomes NEEDS_REVIEW (CLAUDE.md rule 1). The extraction call itself arrives in A5."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

DocumentType = Literal["receipt", "order_confirmation", "screenshot", "product_photo", "other"]
Legibility = Literal["clear", "partial", "poor"]
Confidence = Annotated[float, Field(ge=0.0, le=1.0)]


class ClaimedItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    quantity: int | None = None
    unit_price: str | None = None


class ClaimedFacts(BaseModel):
    model_config = ConfigDict(extra="ignore")

    document_type: DocumentType = "other"
    merchant_name: str | None = None
    order_id: str | None = None
    transaction_id: str | None = None
    order_date: str | None = None  # YYYY-MM-DD
    currency: str | None = None
    total_amount: str | None = None  # decimal string
    items: list[ClaimedItem] = Field(default_factory=list)
    payer_name: str | None = None
    payer_email: str | None = None
    shipping_address: str | None = None
    visible_damage_description: str | None = None
    embedded_instructions_detected: bool = False
    # Kept for the merchant's audit view only. It never goes into a prompt or a check table.
    embedded_instructions_text: str | None = None
    legibility: Legibility = "clear"
    field_confidence: dict[str, Confidence] = Field(default_factory=dict)
