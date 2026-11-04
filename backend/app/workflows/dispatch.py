"""How an analysis gets started: Render Workflows when configured, otherwise a local background
task. If Render cannot be reached, the case is analysed locally instead of being left waiting."""

import logging
from collections.abc import Callable
from typing import Any, Protocol

from fastapi import BackgroundTasks, FastAPI

from app.config import Settings
from app.workflows.pipeline import analyze_case, mark_failed

logger = logging.getLogger(__name__)

TASK_NAME = "analyze_case"


class Dispatcher(Protocol):
    async def enqueue(self, case_id: str, background: BackgroundTasks) -> None: ...


async def run_locally(app: FastAPI, case_id: str) -> None:
    """The background-task path. A crash marks the case `error` so the merchant can re-run it."""
    state = app.state
    try:
        await analyze_case(
            case_id,
            settings=state.settings,
            engine=state.engine,
            paypal=state.paypal,
            groq=state.groq,
        )
    except Exception as exc:
        logger.exception("analysis_crashed", extra={"case_id": case_id})
        mark_failed(state.engine, case_id, exc)


class LocalDispatcher:
    def __init__(self, app: FastAPI) -> None:
        self._app = app

    async def enqueue(self, case_id: str, background: BackgroundTasks) -> None:
        background.add_task(run_locally, self._app, case_id)


class RenderDispatcher:
    """Starts the `analyze_case` task on Render Workflows (retries are configured on the task)."""

    def __init__(
        self,
        app: FastAPI,
        settings: Settings,
        client_factory: Callable[[], Any] | None = None,
    ) -> None:
        self._local = LocalDispatcher(app)
        self._slug = f"{settings.render_workflow_slug}/{TASK_NAME}"
        self._token = settings.render_api_key
        self._factory = client_factory

    def _client(self) -> Any:
        if self._factory is not None:
            return self._factory()
        from render import RenderAsync  # imported here so the API does not need it otherwise

        return RenderAsync(token=self._token)

    async def enqueue(self, case_id: str, background: BackgroundTasks) -> None:
        try:
            await self._client().workflows.start_task(
                self._slug, [case_id], idempotency_key=f"analyze:{case_id}"
            )
        except Exception:
            logger.warning("render_dispatch_failed", extra={"case_id": case_id})
            await self._local.enqueue(case_id, background)


def make_dispatcher(app: FastAPI, settings: Settings) -> Dispatcher:
    if (
        settings.render_workflows_enabled
        and settings.render_api_key
        and settings.render_workflow_slug
    ):
        return RenderDispatcher(app, settings)
    return LocalDispatcher(app)
