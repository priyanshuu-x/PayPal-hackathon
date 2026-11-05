"""Draft the note that goes out with an action: a refund note, or a dispute evidence note.

The model only words it: facts come from PayPal's record, and drafting/grounding.py decides
whether the wording may be used. Three tries, then a template."""

import json
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from app.drafting.grounding import (
    MAX_DISPUTE_NOTE_CHARS,
    MAX_NOTE_CHARS,
    GroundingFacts,
    GroundingItem,
    GroundingResult,
    check_grounding,
)
from app.drafting.prompts import (
    DISPUTE_SYSTEM,
    DISPUTE_VERSION,
    DRAFT_SYSTEM,
    DRAFT_VERSION,
    build_user_message,
)
from app.drafting.templates import TEMPLATE_VERSION, dispute_note, refund_note
from app.llm.groq_client import GroqClient, LLMError

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3  # the first try plus two regenerations
_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)

NoteKind = Literal["refund", "dispute"]
_KIND_SYSTEM = {"refund": DRAFT_SYSTEM, "dispute": DISPUTE_SYSTEM}
_KIND_VERSION = {"refund": DRAFT_VERSION, "dispute": DISPUTE_VERSION}
_KIND_MAX_CHARS = {"refund": MAX_NOTE_CHARS, "dispute": MAX_DISPUTE_NOTE_CHARS}


def max_chars_for(kind: NoteKind) -> int:
    return _KIND_MAX_CHARS[kind]


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


def prompt_facts(
    facts: GroundingFacts,
    store_name: str,
    kind: NoteKind = "refund",
    tracking_status: str | None = None,
) -> str:
    """The only data the model sees: record fields, nothing from any uploaded document and
    nothing the buyer wrote."""
    body: dict[str, Any] = {
        "store_name": store_name,
        "order_id": facts.order_id,
        "currency": facts.currency,
        "order_date": facts.order_date.isoformat(),
        "items": [{"name": i.name, "quantity": i.quantity} for i in facts.items],
    }
    if kind == "refund":
        body["refund_amount"] = str(facts.amount)
    else:
        body["order_amount"] = str(facts.amount)
        body["tracking_number"] = facts.tracking_number
        body["tracking_status"] = tracking_status
    return json.dumps(body, indent=2)


def _clean(reply: str) -> str:
    text = _THINK.sub("", reply).strip().strip('"').strip()
    return " ".join(text.split())


def _grade(text: str, facts: GroundingFacts, max_chars: int) -> GroundingResult:
    if not text:
        return GroundingResult(False, (), ("the note was empty",))
    return check_grounding(text, facts, max_chars)


async def draft_note(
    client: GroqClient,
    *,
    kind: NoteKind,
    model: str,
    facts: GroundingFacts,
    store_name: str,
    consume_budget: Callable[[], bool],
    tracking_status: str | None = None,
) -> DraftResult:
    facts_json = prompt_facts(facts, store_name, kind, tracking_status)
    max_chars = _KIND_MAX_CHARS[kind]
    problems: list[str] | None = None
    for _ in range(MAX_ATTEMPTS):
        if not consume_budget():
            break
        messages = [
            {"role": "system", "content": _KIND_SYSTEM[kind]},
            {"role": "user", "content": build_user_message(facts_json, problems)},
        ]
        try:
            reply = await client.chat(
                model=model, messages=messages, max_tokens=1200, json_mode=False
            )
        except LLMError:
            break
        text = _clean(reply)
        result = _grade(text, facts, max_chars)
        if result.grounded:
            return DraftResult(text, True, (), _KIND_VERSION[kind], model)
        problems = [*result.issues, *(f"'{v}' is not in the facts" for v in result.unsupported)]
        if not text:
            problems = ["the note was empty"]
        logger.info("draft_rejected", extra={"attempt_problems": len(problems)})

    note = (
        refund_note(facts, store_name) if kind == "refund" else dispute_note(facts, tracking_status)
    )
    return DraftResult(note, True, (), TEMPLATE_VERSION, None)


async def draft_refund_note(
    client: GroqClient,
    *,
    model: str,
    facts: GroundingFacts,
    store_name: str,
    consume_budget: Callable[[], bool],
) -> DraftResult:
    return await draft_note(
        client,
        kind="refund",
        model=model,
        facts=facts,
        store_name=store_name,
        consume_budget=consume_budget,
    )
