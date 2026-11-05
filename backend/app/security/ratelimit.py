"""Per-IP rate limiting (threat model T17: floods that burn LLM quota or Render credits).

A sliding-window counter held in memory. That is right for a single web service instance, which
is what this deployment is; with several instances each would count on its own. The client IP
comes from the socket, or from X-Forwarded-For only when TRUSTED_PROXY_COUNT says how many proxies
sit in front of us (Render adds one), so a client cannot spoof its own IP.
"""

import ipaddress
import time
from collections import deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

MAX_TRACKED_KEYS = 10_000


@dataclass(frozen=True)
class Rule:
    name: str
    limit: int
    window_seconds: int


GENERAL = Rule("general", 240, 60)
LOGIN = Rule("login", 10, 60)
REFUNDS = Rule("refunds", 5, 600)
STORE_WRITE = Rule("store_write", 30, 60)
STORE_SDK = Rule("store_sdk", 30, 60)
EVIDENCE = Rule("evidence", 20, 600)
DEMO = Rule("demo", 10, 60)
WEBHOOKS = Rule("webhooks", 300, 60)


def rule_for(method: str, path: str) -> Rule:
    """The limit that applies to a request. Everything under /api has at least GENERAL."""
    if method == "POST":
        if path == "/api/auth/login":
            return LOGIN
        if path == "/api/refunds":
            return REFUNDS
        if path.startswith("/api/store/orders"):
            return STORE_WRITE
        if path.startswith("/api/cases/") and path.endswith("/evidence"):
            return EVIDENCE
        if path.startswith("/api/demo/"):
            return DEMO
        if path.startswith("/api/webhooks/"):
            return WEBHOOKS
    elif method == "GET" and path == "/api/store/sdk-config":
        return STORE_SDK
    return GENERAL


class SlidingWindowLimiter:
    def __init__(self, max_keys: int = MAX_TRACKED_KEYS) -> None:
        self._max_keys = max_keys
        self._hits: dict[str, deque[float]] = {}

    def check(self, key: str, rule: Rule, *, now: float | None = None) -> tuple[bool, int]:
        """Count one request. Returns (allowed, seconds until the next one would be allowed)."""
        current = time.monotonic() if now is None else now
        hits = self._hits.setdefault(f"{rule.name}:{key}", deque())
        while hits and current - hits[0] >= rule.window_seconds:
            hits.popleft()
        if len(hits) >= rule.limit:
            retry_after = int(rule.window_seconds - (current - hits[0])) + 1
            return False, max(retry_after, 1)
        hits.append(current)
        if len(self._hits) > self._max_keys:
            self._evict(current)
        return True, 0

    def _evict(self, now: float) -> None:
        """Bound memory: drop idle keys first, then the least recently used tenth."""
        idle = [k for k, h in self._hits.items() if not h or now - h[-1] >= 3600]
        for key in idle:
            del self._hits[key]
        if len(self._hits) > self._max_keys:
            oldest = sorted(self._hits, key=lambda k: self._hits[k][-1])
            for key in oldest[: max(len(oldest) // 10, 1)]:
                del self._hits[key]


def client_ip(headers: Mapping[str, str], peer: str | None, *, trusted_proxies: int = 0) -> str:
    """The caller's IP. X-Forwarded-For is only believed for the number of trusted proxies,
    counted from the right (the entries our own proxies added)."""
    fallback = peer or "unknown"
    forwarded = headers.get("x-forwarded-for")
    if trusted_proxies <= 0 or not forwarded:
        return fallback
    parts: Sequence[str] = [p.strip() for p in forwarded.split(",") if p.strip()]
    index = len(parts) - trusted_proxies
    if index < 0:
        return fallback
    try:
        return str(ipaddress.ip_address(parts[index]))
    except ValueError:
        return fallback
