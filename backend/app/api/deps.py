"""Shared FastAPI dependencies."""

import secrets
from collections.abc import Iterator

from fastapi import Request, Response
from sqlmodel import Session

from app.config import Settings, get_settings
from app.paypal.client import PayPalClient

BUYER_COOKIE = "proofcheck_buyer"
BUYER_COOKIE_MAX_AGE = 30 * 24 * 3600


def settings_dep() -> Settings:
    return get_settings()


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
