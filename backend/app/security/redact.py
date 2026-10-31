"""Redaction for logs and error messages. Everything logged goes through here."""

import re
from collections.abc import Mapping, Sequence
from typing import Any

REDACTED = "[REDACTED]"

_SENSITIVE_KEY = re.compile(
    r"(secret|password|passwd|token|authorization|api[_-]?key|license|cookie|csrf|session)",
    re.IGNORECASE,
)

_TEXT_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\bA[0-9A-Za-z_-]{60,}\b"),  # PayPal-style client IDs / access tokens
    re.compile(
        r"(?i)\b(client_secret|access_token|api_key|password|secret|token)\s*[=:]\s*[^\s,;&\"']+"
    ),
)

_EMAIL = re.compile(r"\b([A-Za-z0-9._%+-])[A-Za-z0-9._%+-]*@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")


def redact_text(text: str) -> str:
    """Mask credentials and shorten email addresses inside free text."""
    for pattern in _TEXT_PATTERNS:
        text = pattern.sub(REDACTED, text)
    return _EMAIL.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", text)


def redact(value: Any) -> Any:
    """Recursively redact a JSON-like structure (dict, list, str, scalars)."""
    if isinstance(value, Mapping):
        return {
            k: REDACTED if _SENSITIVE_KEY.search(str(k)) else redact(v) for k, v in value.items()
        }
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Sequence) and not isinstance(value, bytes | bytearray):
        return [redact(v) for v in value]
    return value
