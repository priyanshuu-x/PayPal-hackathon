"""The only REST client for PayPal: OAuth token cache, retries, idempotency headers.

Sandbox only (the base URL is validated in config). Never log or return response bodies.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.config import Settings
from app.paypal.errors import PayPalError

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(connect=5.0, read=20.0, write=20.0, pool=5.0)
MAX_ATTEMPTS = 4  # first try plus up to 3 retries on 429 / 5xx
TOKEN_SAFETY_SECONDS = 60
MAX_RETRY_WAIT = 5.0

Sleep = Callable[[float], Awaitable[None]]


class _Token:
    def __init__(self, value: str, expires_at: float) -> None:
        self.value = value
        self.expires_at = expires_at

    def valid(self) -> bool:
        return time.monotonic() < self.expires_at


class PayPalClient:
    def __init__(
        self,
        settings: Settings,
        *,
        http: httpx.AsyncClient | None = None,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        self._settings = settings
        self._http = http or httpx.AsyncClient(base_url=settings.paypal_base_url, timeout=TIMEOUT)
        self._sleep = sleep
        self._token: _Token | None = None
        self._token_lock = asyncio.Lock()

    async def aclose(self) -> None:
        await self._http.aclose()

    # --- OAuth -----------------------------------------------------------------------

    async def _fetch_token(self, extra: dict[str, str] | None = None) -> tuple[str, int]:
        data = {"grant_type": "client_credentials", **(extra or {})}
        response: httpx.Response | None = None
        for attempt in range(MAX_ATTEMPTS):
            try:
                response = await self._http.post(
                    "/v1/oauth2/token",
                    data=data,
                    auth=(self._settings.paypal_client_id, self._settings.paypal_client_secret),
                    headers={"Accept": "application/json"},
                )
                break
            except httpx.HTTPError as exc:
                if attempt + 1 == MAX_ATTEMPTS:
                    raise PayPalError("PayPal token request failed", details=str(exc)) from exc
                await self._sleep(_backoff(attempt, None))
        assert response is not None  # noqa: S101  (loop always sets it or raises)
        if response.status_code != 200:
            raise _error_from(response, "PayPal token request rejected")
        body = response.json()
        return str(body["access_token"]), int(body.get("expires_in", 300))

    async def _access_token(self, *, force: bool = False) -> str:
        async with self._token_lock:
            if force or self._token is None or not self._token.valid():
                value, expires_in = await self._fetch_token()
                lifetime = max(expires_in - TOKEN_SAFETY_SECONDS, 1)
                self._token = _Token(value, time.monotonic() + lifetime)
            return self._token.value

    async def client_token(self, domains: list[str] | None = None) -> str:
        """Browser-safe token for PayPal JS SDK v6 (`createInstance({ clientToken })`).

        Short-lived (about 15 minutes). The caller caches it by expiry; see routes_store.
        `intent=sdk_init` appears in one docs.paypal.ai sample but not in the developer.paypal.com
        reference, so it is not sent. Confirm against the sandbox (Phase 0 spike 4).
        """
        extra = {"response_type": "client_token"}
        if domains:
            for index, domain in enumerate(domains):
                extra[f"domains[{index}]"] = domain
        value, _ = await self._fetch_token(extra)
        return value

    # --- Requests --------------------------------------------------------------------

    async def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        request_id: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Call PayPal and return the parsed JSON body (empty dict for 204).

        `request_id` becomes the `PayPal-Request-Id` header and is reused on every retry, so a
        retried create, capture or refund can never execute twice.
        """
        refreshed = False
        for attempt in range(MAX_ATTEMPTS):
            headers = {"Accept": "application/json"}
            if request_id:
                headers["PayPal-Request-Id"] = request_id
            headers.update(extra_headers or {})
            headers["Authorization"] = f"Bearer {await self._access_token()}"
            try:
                response = await self._http.request(method, path, json=json, headers=headers)
            except httpx.HTTPError as exc:
                if attempt + 1 < MAX_ATTEMPTS:
                    await self._sleep(_backoff(attempt, None))
                    continue
                raise PayPalError("PayPal request failed", details=str(exc)) from exc

            if response.status_code == 401 and not refreshed:
                refreshed = True
                await self._access_token(force=True)
                continue
            if response.status_code in (429, 500, 502, 503, 504) and attempt + 1 < MAX_ATTEMPTS:
                await self._sleep(_backoff(attempt, response.headers.get("Retry-After")))
                continue
            if response.is_success:
                return response.json() if response.content else {}
            raise _error_from(response, f"PayPal {method} {path} failed")
        raise PayPalError("PayPal request failed after retries")  # pragma: no cover


def _backoff(attempt: int, retry_after: str | None) -> float:
    if retry_after and retry_after.isdigit():
        return min(float(retry_after), MAX_RETRY_WAIT)
    return min(0.5 * (2**attempt), MAX_RETRY_WAIT)


def _error_from(response: httpx.Response, message: str) -> PayPalError:
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text[:500]
    name = body.get("name") if isinstance(body, dict) else None
    debug_id = body.get("debug_id") if isinstance(body, dict) else None
    logger.warning(
        "paypal_error",
        extra={
            "paypal_status": response.status_code,
            "paypal_error": name,
            "paypal_debug_id": debug_id,
            "paypal_body": body,
        },
    )
    return PayPalError(
        message,
        status_code=response.status_code,
        name=name,
        debug_id=debug_id,
        details=body,
    )
