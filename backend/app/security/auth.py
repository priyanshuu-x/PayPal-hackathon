"""Merchant authentication: scrypt password check and signed, expiring session tokens.

Generate a hash for DEMO_MERCHANT_PASSWORD_HASH with:  python -m app.security.auth
"""

import base64
import getpass
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass

SESSION_TTL_SECONDS = 8 * 3600
MIN_SECRET_LENGTH = 32
_SCRYPT_N, _SCRYPT_R, _SCRYPT_P = 2**14, 8, 1
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 300


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R, p=_SCRYPT_P, dklen=32
    )
    return f"scrypt:{_SCRYPT_N}:{_SCRYPT_R}:{_SCRYPT_P}:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time check. Any malformed stored hash simply fails."""
    try:
        scheme, n, r, p, salt_b64, digest_b64 = stored.split(":")
        if scheme != "scrypt":
            return False
        expected = _unb64(digest_b64)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_unb64(salt_b64),
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


@dataclass(frozen=True)
class MerchantSession:
    merchant_id: str
    csrf: str
    expires_at: int


def _sign(secret: str, body: str) -> str:
    return _b64(hmac.new(secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest())


def issue_session(
    secret: str, merchant_id: str, *, now: float | None = None, ttl: int = SESSION_TTL_SECONDS
) -> tuple[str, MerchantSession]:
    if len(secret) < MIN_SECRET_LENGTH:
        raise ValueError("SESSION_SECRET must be at least 32 characters")
    session = MerchantSession(
        merchant_id=merchant_id,
        csrf=secrets.token_urlsafe(32),
        expires_at=int((time.time() if now is None else now) + ttl),
    )
    body = _b64(
        json.dumps(
            {"m": session.merchant_id, "c": session.csrf, "e": session.expires_at},
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{body}.{_sign(secret, body)}", session


def read_session(secret: str, token: str, *, now: float | None = None) -> MerchantSession | None:
    if len(secret) < MIN_SECRET_LENGTH:
        return None
    try:
        body, signature = token.split(".")
        if not hmac.compare_digest(signature, _sign(secret, body)):
            return None
        data = json.loads(_unb64(body))
        session = MerchantSession(str(data["m"]), str(data["c"]), int(data["e"]))
    except (ValueError, KeyError, TypeError):
        return None
    if session.expires_at <= (time.time() if now is None else now):
        return None
    return session


def csrf_matches(session: MerchantSession, supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(session.csrf, supplied or "")


class LoginThrottle:
    """Per-key failed-login limiter, in memory. Global rate limiting arrives in A10."""

    def __init__(self) -> None:
        self._failures: dict[str, list[float]] = {}

    def _recent(self, key: str, now: float) -> list[float]:
        recent = [t for t in self._failures.get(key, []) if now - t < FAILURE_WINDOW_SECONDS]
        self._failures[key] = recent
        return recent

    def blocked(self, key: str, *, now: float | None = None) -> bool:
        return len(self._recent(key, time.time() if now is None else now)) >= MAX_FAILURES

    def record_failure(self, key: str, *, now: float | None = None) -> None:
        current = time.time() if now is None else now
        self._recent(key, current).append(current)

    def reset(self, key: str) -> None:
        self._failures.pop(key, None)


if __name__ == "__main__":  # pragma: no cover
    print(hash_password(getpass.getpass("Merchant password: ")))
