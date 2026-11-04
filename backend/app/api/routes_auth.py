"""Merchant login. The demo has one merchant, configured through the environment."""

import hmac
from functools import lru_cache
from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response
from pydantic import BaseModel, ConfigDict, Field

from app.api.deps import (
    DEMO_MERCHANT_ID,
    SESSION_COOKIE,
    merchant_csrf_dep,
    merchant_dep,
    settings_dep,
)
from app.api.errors import ApiError
from app.config import Settings
from app.security.auth import (
    MIN_SECRET_LENGTH,
    SESSION_TTL_SECONDS,
    LoginThrottle,
    MerchantSession,
    hash_password,
    issue_session,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


class LoginIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=254)
    password: str = Field(min_length=1, max_length=200)


@lru_cache
def _dummy_hash() -> str:
    """Verified against when the email is wrong, so timing does not reveal the account."""
    return hash_password("not-the-password")


def _throttle(request: Request) -> LoginThrottle:
    throttle: LoginThrottle = request.app.state.login_throttle
    return throttle


@router.post("/login")
async def login(
    body: LoginIn,
    request: Request,
    response: Response,
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, str]:
    if (
        len(settings.session_secret) < MIN_SECRET_LENGTH
        or not settings.demo_merchant_email
        or not settings.demo_merchant_password_hash
    ):
        raise ApiError(503, "internal_error", "Sign-in is not configured.")

    client_ip = request.client.host if request.client else "unknown"
    key = f"{client_ip}:{body.email.strip().lower()}"
    throttle = _throttle(request)
    if throttle.blocked(key):
        raise ApiError(429, "rate_limited", "Too many attempts. Try again in a few minutes.")

    email_ok = hmac.compare_digest(
        body.email.strip().lower().encode(), settings.demo_merchant_email.strip().lower().encode()
    )
    stored = settings.demo_merchant_password_hash if email_ok else _dummy_hash()
    password_ok = verify_password(body.password, stored)
    if not (email_ok and password_ok):
        throttle.record_failure(key)
        raise ApiError(401, "unauthenticated", "Incorrect email or password.")

    throttle.reset(key)
    token, session = issue_session(settings.session_secret, DEMO_MERCHANT_ID)
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=settings.cookie_secure,
        path="/api",
    )
    return {"merchant_id": session.merchant_id, "csrf_token": session.csrf}


@router.post("/logout", status_code=204)
async def logout(
    response: Response, _: Annotated[MerchantSession, Depends(merchant_csrf_dep)]
) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/api")


@router.get("/me")
async def me(session: Annotated[MerchantSession, Depends(merchant_dep)]) -> dict[str, str]:
    return {"merchant_id": session.merchant_id, "csrf_token": session.csrf}
