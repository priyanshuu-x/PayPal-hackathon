"""Draft the refund note. The model only words it: facts come from PayPal's record, and
drafting/grounding.py decides whether the wording may be used. Three tries, then a template."""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

from app.drafting.grounding import (
    GroundingFacts,
    GroundingItem,
    GroundingResult,
    check_grounding,
)
from app.drafting.prompts import DRAFT_SYSTEM, DRAFT_VERSION, build_user_message
from app.drafting.templates import TEMPLATE_VERSION, refund_note
from app.llm.groq_client import GroqClient, LLMError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # the first try plus two regenerations
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class DraftResult:
    text: str
    grounded: bool
    unsupported: tuple[str, ...]
    prompt_version: str
    model_id: str | None  # None for the template fallback


def facts_from_snapshot(snapshot: dict[str, Any], capture_id: str | None) -> GroundingFacts | None:
    """Build grounding facts from the PayPal record snapshot stored on the case."""
    try:
        created = datetime.fromisoformat(str(snapshot["created"]))
        return GroundingFacts(
            order_id=str(snapshot["order_id"]),
            amount=Decimal(str(snapshot["amount"])),
            currency=str(snapshot["currency"]),
            order_date=created.astimezone(UTC).date() if created.tzinfo else created.date(),
            capture_ids=(capture_id,) if capture_id else (),
            tracking_number=snapshot.get("tracking_number"),
            items=tuple(
                GroundingItem(str(i["name"]), int(i["quantity"]), Decimal(str(i["unit_price"])))
                for i in snapshot.get("items", [])
            ),
        )
    except (KeyError, ValueError, TypeError, InvalidOperation):
        return None


def prompt_facts(facts: GroundingFacts, store_name: str) -> str:
    """The only data the model sees: record fields, nothing from the uploaded document."""
    return json.dumps(
        {
            "store_name": store_name,
            "order_id": facts.order_id,
            "refund_amount": str(facts.amount),
            "currency": facts.currency,
            "order_date": facts.order_date.isoformat(),
            "items": [{"name": i.name, "quantity": i.quantity} for i in facts.items],
        },
        indent=2,
    )


def _clean(reply: str) -> str:
    text = _THINK.sub("", reply).strip().strip('"').strip()
    return " ".join(text.split())


async def draft_refund_note(
    client: GroqClient,
    *,
    model: str,
    facts: GroundingFacts,
    store_name: str,
    consume_budget: Callable[[], bool],
) -> DraftResult:
    facts_json = prompt_facts(facts, store_name)
    problems: list[str] | None = None
    for _ in range(MAX_ATTEMPTS):
        if not consume_budget():
            break
        messages = [
            {"role": "system", "content": DRAFT_SYSTEM},
            {"role": "user", "content": build_user_message(facts_json, problems)},
        ]
        try:
            reply = await client.chat(
                model=model, messages=messages, max_tokens=1200, json_mode=False
            )
        except LLMError:
            break
        text = _clean(reply)
        result = _grade(text, facts)
        if result.grounded:
            return DraftResult(text, True, (), DRAFT_VERSION, model)
        problems = [*result.issues, *(f"'{v}' is not in the facts" for v in result.unsupported)]
        if not text:
            problems = ["the note was empty"]
        logger.info("draft_rejected", extra={"attempt_problems": len(problems)})

    note = refund_note(facts, store_name)
    return DraftResult(note, True, (), TEMPLATE_VERSION, None)


def _grade(text: str, facts: GroundingFacts) -> GroundingResult:
    if not text:
        return GroundingResult(False, (), ("the note was empty",))
    return check_grounding(text, facts)
