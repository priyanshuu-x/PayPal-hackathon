"""FastAPI app. The Copilot allowlist guard runs at startup. CSP and rate limits: A10."""

import asyncio
import contextlib
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import Engine
from sqlmodel import Session

from app.api import (
    routes_auth,
    routes_cases,
    routes_copilot,
    routes_demo,
    routes_eval,
    routes_insights,
    routes_refunds,
    routes_store,
    routes_webhooks,
)
from app.api.errors import register_error_handlers
from app.config import get_settings
from app.copilot.agent import CaseCopilot, make_chat_model
from app.copilot.throttle import QuestionThrottle
from app.copilot.toolkit import assert_read_only, build_toolkit_tools
from app.db.session import create_db_and_tables, make_engine
from app.llm.groq_client import GroqClient
from app.logging_config import configure_logging
from app.middleware import BodySizeLimitMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware
from app.paypal.client import PayPalClient
from app.security.auth import LoginThrottle
from app.security.ratelimit import SlidingWindowLimiter
from app.security.retention import purge_expired
from app.workflows.dispatch import make_dispatcher

logger = logging.getLogger(__name__)

RETENTION_INTERVAL_SECONDS = 24 * 3600


async def _retention_loop(engine: Engine, upload_dir: str, retention_days: int) -> None:
    """Delete expired uploads and personal data now, then once a day (threat model T15)."""
    while True:
        try:
            with Session(engine) as db:
                report = purge_expired(db, Path(upload_dir), retention_days=retention_days)
            if report.changed:
                logger.info("retention_run", extra={"report": report.__dict__})
        except Exception:
            logger.exception("retention_failed")
        await asyncio.sleep(RETENTION_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    # Copilot allowlist guard (CLAUDE.md rule 3): refuse to boot unless the Copilot's tools are
    # exactly the read-only allowlist. Runs before anything else is set up.
    copilot_tools = build_toolkit_tools(settings)
    assert_read_only(copilot_tools)

    engine = make_engine(settings.database_url)
    create_db_and_tables(engine)
    paypal = PayPalClient(settings)
    groq = GroqClient(settings)
    app.state.copilot = (
        CaseCopilot(
            copilot_tools,
            make_chat_model(settings),
            max_tool_calls=settings.copilot_max_tool_calls,
            model_id=settings.groq_model_text,
        )
        if settings.groq_api_key
        else None
    )
    app.state.copilot_throttle = QuestionThrottle()
    app.state.settings = settings
    app.state.engine = engine
    app.state.paypal = paypal
    app.state.groq = groq
    app.state.login_throttle = LoginThrottle()
    app.state.dispatcher = make_dispatcher(app, settings)
    retention = asyncio.create_task(
        _retention_loop(engine, settings.upload_dir, settings.retention_days)
    )
    try:
        yield
    finally:
        retention.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await retention
        await paypal.aclose()
        await groq.aclose()
        engine.dispose()


def create_app() -> FastAPI:
    configure_logging()
    settings = get_settings()

    # No Swagger or ReDoc pages (they load scripts from a CDN and invite probing) and no public
    # schema; `make postman` reads the schema with app.openapi().
    app = FastAPI(
        title="ProofCheck API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    register_error_handlers(app)

    # Last added is outermost: security headers wrap everything (including 413 and 429 replies),
    # then CORS (so error replies carry CORS headers and preflights are not rate limited), then
    # the size limit, then the rate limiter closest to the app.
    app.add_middleware(
        RateLimitMiddleware,
        limiter=SlidingWindowLimiter(),
        trusted_proxies=settings.trusted_proxy_count,
        enabled=settings.rate_limit_enabled,
    )
    app.add_middleware(BodySizeLimitMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["GET", "POST"],
        allow_headers=["Content-Type", "X-CSRF-Token"],
    )
    app.add_middleware(SecurityHeadersMiddleware, hsts=settings.cookie_secure)

    @app.get("/api/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    routers = [
        routes_auth.router,
        routes_store.router,
        routes_refunds.router,
        routes_cases.router,
        routes_copilot.router,
        routes_insights.router,
        routes_eval.router,
        routes_webhooks.router,
    ]
    if settings.demo_mode:
        routers.append(routes_demo.router)
    for router in routers:
        app.include_router(router)
    return app


app = create_app()
