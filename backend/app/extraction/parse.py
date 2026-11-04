"""Parse model text into ClaimedFacts. Model output is untrusted data."""

import json
import re

from pydantic import ValidationError

from app.extraction.schema import ClaimedFacts

_THINK = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_FENCE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$")


class ParseError(Exception):
    """The model's reply is not valid ClaimedFacts."""


def parse_claims(text: str) -> ClaimedFacts:
    cleaned = _FENCE.sub("", _THINK.sub("", text).strip()).strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start == -1 or end <= start:
        raise ParseError("no JSON object in the reply")
    try:
        data = json.loads(cleaned[start : end + 1])
        if not isinstance(data, dict):
            raise ParseError("the reply is not a JSON object")
        return ClaimedFacts.model_validate(data)
    except ValueError as exc:  # json errors and pydantic ValidationError are both ValueErrors
        if isinstance(exc, ValidationError):
            raise ParseError("the reply does not match the schema") from None
        raise ParseError("the reply is not valid JSON") from None
