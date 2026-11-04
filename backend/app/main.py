"""FastAPI app. CSP, rate limiting and the Copilot allowlist guard land in later PRs (A8, A10)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import RequestResponseEndpoint

from app.api import (
    routes_auth,
    routes_cases,
    routes_refunds,
    routes_store,
    routes_webhooks,
)
from app.api.errors import register_error_handlers
from app.config import get_settings
from app.db.session import create_db_and_tables, make_engine
from app.logging_config import configure_logging
from app.paypal.client import PayPalClient
from app.security.auth import LoginThrottle


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    create_db_and_tables(engine)
    paypal = PayPalClient(settings)
    app.state.engine = engine
    app.state.paypal = paypal
    app.state.login_throttle = LoginThrottle()
    try:
        yield
    finally:
        await paypal.aclose()
        engine.dispose()


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    app = FastAPI(title="ProofCheck API", version="0.1.0", lifespan=lifespan)
    register_error_handlers(app)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )

    @app.middleware("http")
    async def security_headers(request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Frame-Options"] = "DENY"
        return response

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    for router in (
        routes_auth.router,
        routes_store.router,
        routes_refunds.router,
        routes_cases.router,
        routes_webhooks.router,
    ):
        app.include_router(router)
    return app


app = create_app()
