"""ASGI middleware: request size limits, rate limiting, security headers.

Written as plain ASGI (not BaseHTTPMiddleware) so a request can be refused before its body is
read, and so a streaming upload cannot be buffered past the limit.
"""

import json
from collections.abc import Callable
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.security.ratelimit import SlidingWindowLimiter, client_ip, rule_for
from app.security.uploads import MAX_BYTES

Headers = list[tuple[bytes, bytes]]

MAX_UPLOAD_REQUEST_BYTES = MAX_BYTES + 256 * 1024  # the file plus multipart framing
MAX_JSON_REQUEST_BYTES = 1024 * 1024
HEALTH_PATH = "/api/health"

# A JSON API has no pages: nothing may be loaded, framed or submitted from its responses.
CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"


def _error_body(code: str, message: str) -> bytes:
    return json.dumps({"error": {"code": code, "message": message}}).encode("utf-8")


async def _respond(
    send: Send, status: int, code: str, message: str, extra: Headers | None = None
) -> None:
    body = _error_body(code, message)
    headers: Headers = [
        (b"content-type", b"application/json"),
        (b"content-length", str(len(body)).encode("ascii")),
        *(extra or []),
    ]
    await send({"type": "http.response.start", "status": status, "headers": headers})
    await send({"type": "http.response.body", "body": body})


def _is_upload(method: str, path: str) -> bool:
    return method == "POST" and (
        path == "/api/refunds" or (path.startswith("/api/cases/") and path.endswith("/evidence"))
    )


class RequestTooLarge(Exception):
    pass


class BodySizeLimitMiddleware:
    """Refuse oversized request bodies, by Content-Length up front and by counting bytes for
    streamed or chunked bodies."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or not scope["path"].startswith("/api"):
            await self.app(scope, receive, send)
            return
        limit = (
            MAX_UPLOAD_REQUEST_BYTES
            if _is_upload(scope["method"], scope["path"])
            else MAX_JSON_REQUEST_BYTES
        )
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > limit:
            await _respond(send, 413, "payload_too_large", "The request is too large.")
            return

        received = 0

        async def counted() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    raise RequestTooLarge
            return message

        started = False

        async def tracking_send(message: Message) -> None:
            nonlocal started
            started = started or message["type"] == "http.response.start"
            await send(message)

        try:
            await self.app(scope, counted, tracking_send)
        except RequestTooLarge:
            if not started:
                await _respond(send, 413, "payload_too_large", "The request is too large.")


class RateLimitMiddleware:
    def __init__(
        self,
        app: ASGIApp,
        *,
        limiter: SlidingWindowLimiter,
        trusted_proxies: int = 0,
        rule_lookup: Callable[[str, str], Any] = rule_for,
        enabled: bool = True,
    ) -> None:
        self.app = app
        self._limiter = limiter
        self._trusted_proxies = trusted_proxies
        self._rule_for = rule_lookup
        self._enabled = enabled

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope["path"] if scope["type"] == "http" else ""
        if not self._enabled or not path.startswith("/api") or path == HEALTH_PATH:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        peer = scope["client"][0] if scope.get("client") else None
        ip = client_ip(headers, peer, trusted_proxies=self._trusted_proxies)
        allowed, retry_after = self._limiter.check(ip, self._rule_for(scope["method"], path))
        if not allowed:
            await _respond(
                send,
                429,
                "rate_limited",
                "Too many requests. Try again shortly.",
                [(b"retry-after", str(retry_after).encode("ascii"))],
            )
            return
        await self.app(scope, receive, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, *, hsts: bool) -> None:
        self.app = app
        self._hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        is_api = scope["path"].startswith("/api")

        async def with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers: Headers = list(message.get("headers", []))  # keep repeated headers
                present = {name.lower() for name, _ in headers}
                defaults = {
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"no-referrer",
                    b"x-frame-options": b"DENY",
                    b"content-security-policy": CSP.encode("ascii"),
                    b"permissions-policy": b"camera=(), microphone=(), geolocation=(), payment=()",
                    b"cross-origin-opener-policy": b"same-origin",
                    b"cross-origin-resource-policy": b"same-site",
                }
                if self._hsts:
                    defaults[b"strict-transport-security"] = b"max-age=31536000; includeSubDomains"
                if is_api:
                    defaults[b"cache-control"] = b"no-store"
                headers += [(n, v) for n, v in defaults.items() if n not in present]
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, with_headers)
