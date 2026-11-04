"""Shared FastAPI dependencies."""

import secrets
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request, Response
from sqlmodel import Session

from app.api.errors import ApiError
from app.config import Settings, get_settings
from app.paypal.client import PayPalClient
from app.security.auth import MerchantSession, csrf_matches, read_session

BUYER_COOKIE = "proofcheck_buyer"
BUYER_COOKIE_MAX_AGE = 30 * 24 * 3600


SESSION_COOKIE = "proofcheck_session"
DEMO_MERCHANT_ID = "demo"


def settings_dep() -> Settings:
    return get_settings()


def merchant_dep(request: Request) -> MerchantSession:
    """Require a valid merchant session cookie."""
    token = request.cookies.get(SESSION_COOKIE)
    session = read_session(get_settings().session_secret, token) if token else None
    if session is None:
        raise ApiError(401, "unauthenticated", "Please sign in.")
    return session


def merchant_csrf_dep(
    request: Request, session: Annotated[MerchantSession, Depends(merchant_dep)]
) -> MerchantSession:
    """Merchant session plus a matching X-CSRF-Token header. Use on every merchant POST."""
    if not csrf_matches(session, request.headers.get("X-CSRF-Token")):
        raise ApiError(403, "csrf_failed", "The request could not be verified.")
    return session


def paypal_dep(request: Request) -> PayPalClient:
    client: PayPalClient = request.app.state.paypal
    return client


def db_dep(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


def buyer_dep(request: Request, response: Response) -> str:
    """Anonymous buyer session: a random ID in an HttpOnly cookie. Holds no data itself."""
    existing = request.cookies.get(BUYER_COOKIE)
    if existing and 20 <= len(existing) <= 100:
        return existing
    buyer_id = secrets.token_urlsafe(32)
    response.set_cookie(
        BUYER_COOKIE,
        buyer_id,
        max_age=BUYER_COOKIE_MAX_AGE,
        httponly=True,
        samesite="lax",
        secure=get_settings().cookie_secure,
        path="/api",
    )
    return buyer_id
