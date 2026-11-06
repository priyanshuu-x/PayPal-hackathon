"""Minimal async client for Groq's OpenAI-compatible chat API.

Used for extraction and drafting, which have NO tools and NO credentials in their prompts
(CLAUDE.md rule 2): this client never sends `tools`, and the API key only travels in the
Authorization header. Response bodies are never put into exception messages.
"""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import Settings

logger = logging.getLogger(__name__)

GROQ_BASE_URL = "https://api.groq.com/openai/v1"
TIMEOUT = httpx.Timeout(connect=5.0, read=60.0, write=30.0, pool=5.0)
MAX_ATTEMPTS = 4  # first try plus up to 3 retries on 429 / 5xx / network errors
MAX_BACKOFF = 10.0  # cap for our own exponential backoff (network errors, 5xx)
# Groq's free tier limits the vision model to 7000 input tokens a minute (about four receipts), and
# answers 429 with how long to wait (usually 20 to 30 s). Analysis runs in the background, so
# waiting that long beats failing the case; the cap protects against absurd values.
MAX_RETRY_AFTER = 60.0

Sleep = Callable[[float], Awaitable[None]]


class LLMError(Exception):
    """The model call failed. The message is generic; details go to redacted logs."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class GroqClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._api_key = settings.groq_api_key
        self._http = http or httpx.AsyncClient(base_url=GROQ_BASE_URL, timeout=TIMEOUT)
        self._sleep = sleep

    async def aclose(self) -> None:
        await self._http.aclose()

    async def chat(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        max_tokens: int = 1500,
        json_mode: bool = True,
    ) -> str:
        """One chat completion at temperature 0. Returns the message text."""
        if not self._api_key:
            raise LLMError("The AI service is not configured.")
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0,
            "max_tokens": max_tokens,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}

        for attempt in range(MAX_ATTEMPTS):
            last = attempt + 1 == MAX_ATTEMPTS
            try:
                response = await self._http.post(
                    "/chat/completions",
                    json=body,
                    headers={"Authorization": f"Bearer {self._api_key}"},
                )
            except httpx.HTTPError as exc:
                logger.warning("llm_transport_error", extra={"error": type(exc).__name__})
                if last:
                    raise LLMError("The AI service could not be reached.") from None
                await self._sleep(_backoff(attempt, None))
                continue

            if response.status_code in (429, 500, 502, 503, 504) and not last:
                await self._sleep(_backoff(attempt, response.headers.get("Retry-After")))
                continue
            if response.status_code != 200:
                logger.warning(
                    "llm_error",
                    extra={"llm_status": response.status_code, "llm_body": response.text[:500]},
                )
                raise LLMError(
                    "The AI service could not process this request.",
                    status_code=response.status_code,
                )
            return _message_text(response)
        raise LLMError("The AI service failed after retries.")  # pragma: no cover


def _message_text(response: httpx.Response) -> str:
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except (ValueError, KeyError, IndexError, TypeError):
        raise LLMError("The AI service returned an unexpected response.") from None
    if not isinstance(content, str):
        raise LLMError("The AI service returned an unexpected response.")
    return content


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after:
        try:
            return min(float(retry_after) + 0.5, MAX_RETRY_AFTER)
        except ValueError:
            pass
    return min(1.0 * (2**attempt), MAX_BACKOFF)
