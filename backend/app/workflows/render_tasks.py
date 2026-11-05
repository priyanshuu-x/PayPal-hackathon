"""Render Workflows entrypoint. Render starts this as the workflow's worker:

    python -m app.workflows.render_tasks

The task builds its own database engine and API clients from the environment, then runs the same
`analyze_case` the local fallback runs. A crash marks the case `error` and re-raises, so Render's
retry policy runs it again (the analysis is idempotent). Docs: https://render.com/docs/workflows-sdk-python
"""

from render import Retry, Workflows

from app.config import get_settings
from app.db.session import create_db_and_tables, make_engine
from app.llm.groq_client import GroqClient
from app.paypal.client import PayPalClient
from app.workflows.pipeline import analyze_case as run_analysis
from app.workflows.pipeline import mark_failed

RETRY = Retry(max_retries=2, wait_duration_ms=5_000, backoff_scaling=2.0)

app = Workflows(default_retry=RETRY, default_timeout=300)


@app.task(name="analyze_case")  # type: ignore[arg-type]  # the SDK's stub expects a TaskContext
async def analyze_case(case_id: str) -> str:
    settings = get_settings()
    engine = make_engine(settings.database_url)
    create_db_and_tables(engine)
    paypal, groq = PayPalClient(settings), GroqClient(settings)
    try:
        await run_analysis(case_id, settings=settings, engine=engine, paypal=paypal, groq=groq)
    except Exception as exc:
        mark_failed(engine, case_id, exc)
        raise
    finally:
        await paypal.aclose()
        await groq.aclose()
        engine.dispose()
    return case_id


if __name__ == "__main__":  # pragma: no cover
    app.start()
