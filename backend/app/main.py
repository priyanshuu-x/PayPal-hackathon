"""FastAPI app. The Copilot allowlist guard runs at startup. CSP and rate limits: A10."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import RequestResponseEndpoint

from app.api import (
    routes_auth,
    routes_cases,
    routes_copilot,
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
from app.paypal.client import PayPalClient
from app.security.auth import LoginThrottle
from app.workflows.dispatch import make_dispatcher


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
    try:
        yield
    finally:
        await paypal.aclose()
        await groq.aclose()
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
        routes_copilot.router,
        routes_webhooks.router,
    ):
        app.include_router(router)
    return app


app = create_app()
