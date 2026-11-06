"""One vision call (temperature 0, no tools) that turns document images into ClaimedFacts.

A reply that does not fit the schema gets one retry; after that the caller marks the case
NEEDS_REVIEW ("could not read document"). It never silently passes.
"""

import base64
import logging
from collections.abc import Callable
from typing import Any

from app.extraction.parse import ParseError, parse_claims
from app.extraction.prompts import EXTRACT_SYSTEM, EXTRACT_USER
from app.extraction.schema import ClaimedFacts
from app.llm.groq_client import GroqClient, LLMError

logger = logging.getLogger(__name__)

PARSE_ATTEMPTS = 2


class ExtractionFailed(Exception):
    """The document could not be read into ClaimedFacts."""


class LimitReached(Exception):
    """The daily model-call cap was reached before the call was made."""


def build_messages(images: list[bytes]) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = [{"type": "text", "text": EXTRACT_USER}]
    for image in images:
        encoded = base64.b64encode(image).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{encoded}"}}
        )
    return [
        {"role": "system", "content": EXTRACT_SYSTEM},
        {"role": "user", "content": content},
    ]


async def extract_claims(
    client: GroqClient,
    *,
    model: str,
    images: list[bytes],
    consume_budget: Callable[[], bool],
) -> ClaimedFacts:
    messages = build_messages(images)
    for _ in range(PARSE_ATTEMPTS):
        if not consume_budget():
            raise LimitReached
        try:
            reply = await client.chat(model=model, messages=messages, max_tokens=1500)
        except LLMError as exc:
            if exc.rate_limited:  # the provider's usage limit: say so, do not call it unreadable
                raise LimitReached from exc
            raise ExtractionFailed("The document could not be read.") from exc
        try:
            return parse_claims(reply)
        except ParseError as exc:
            logger.warning("extraction_parse_failed", extra={"reason": str(exc)})
    raise ExtractionFailed("The document could not be read.")
