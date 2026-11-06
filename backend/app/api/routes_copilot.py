"""Case Copilot endpoints. Read-only: it can look things up and explain, never change anything."""

import json
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session, col, select

from app.api.deps import db_dep, merchant_csrf_dep, merchant_dep, settings_dep
from app.api.errors import ApiError
from app.api.serializers import iso_z
from app.config import Settings
from app.copilot.agent import CaseContext, CaseCopilot
from app.copilot.throttle import QuestionThrottle
from app.db.models import Case, CheckResult, CopilotTurn
from app.llm.budget import try_consume
from app.security.auth import MerchantSession

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/cases", tags=["copilot"])

MAX_QUESTION_CHARS = 500
HISTORY_LIMIT = 50


class QuestionIn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    question: str = Field(min_length=1, max_length=MAX_QUESTION_CHARS)


def _case(db: Session, case_id: str) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise ApiError(404, "not_found", "Case not found.")
    return case


def build_context(db: Session, case: Case) -> CaseContext:
    """Verified results only. Deliberately left out: the document's claimed values, the buyer's
    reason and email, and the draft (CLAUDE.md rule 5)."""
    checks = db.exec(
        select(CheckResult).where(CheckResult.case_id == case.id).order_by(col(CheckResult.id))
    ).all()
    return CaseContext(
        case_id=case.id,
        status=case.status,
        verdict=case.verdict,
        risk_score=case.risk_score,
        amount=case.amount,
        currency=case.currency,
        paypal_order_id=case.paypal_order_id,
        capture_id=case.capture_id,
        dispute_id=case.dispute_id,
        checks=tuple(
            {
                "field": c.field,
                "result": c.result,
                "severity": c.severity,
                "actual": c.actual,
                "note": c.note,
            }
            for c in checks
        ),
    )


@router.post("/{case_id}/copilot")
async def ask_copilot(
    case_id: str,
    body: QuestionIn,
    request: Request,
    session: Annotated[MerchantSession, Depends(merchant_csrf_dep)],
    db: Annotated[Session, Depends(db_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, Any]:
    case = _case(db, case_id)
    copilot: CaseCopilot | None = getattr(request.app.state, "copilot", None)
    if copilot is None:
        raise ApiError(503, "internal_error", "The Copilot is not available.")
    throttle: QuestionThrottle = request.app.state.copilot_throttle
    if not throttle.allow(session.merchant_id):
        raise ApiError(429, "rate_limited", "Too many questions. Try again in a few minutes.")

    try:
        result = await copilot.ask(
            body.question,
            build_context(db, case),
            lambda: try_consume(db, settings.daily_llm_call_cap),
        )
    except Exception as exc:  # the AI provider is down or over its limit; never a bare 500
        logger.warning("copilot_failed", extra={"case_id": case.id, "error": type(exc).__name__})
        raise ApiError(
            502, "upstream_error", "The assistant is unavailable right now. Try again shortly."
        ) from exc
    names = [call.name for call in result.tool_calls]
    db.add(
        CopilotTurn(
            case_id=case.id,
            question=body.question,
            tools_called_json=json.dumps(
                [{"name": c.name, "args": c.args} for c in result.tool_calls], default=str
            ),
            answer=result.answer,
            prompt_version=result.prompt_version,
            model_id=result.model_id,
        )
    )
    db.commit()
    return {"answer": result.answer, "tools_called": names}


@router.get("/{case_id}/copilot")
async def copilot_history(
    case_id: str,
    _: Annotated[MerchantSession, Depends(merchant_dep)],
    db: Annotated[Session, Depends(db_dep)],
) -> list[dict[str, Any]]:
    _case(db, case_id)
    turns = db.exec(
        select(CopilotTurn)
        .where(CopilotTurn.case_id == case_id)
        .order_by(col(CopilotTurn.id))
        .limit(HISTORY_LIMIT)
    ).all()
    return [
        {
            "question": t.question,
            "answer": t.answer,
            "tools_called": [c["name"] for c in json.loads(t.tools_called_json)],
            "created_at": iso_z(t.created_at),
        }
        for t in turns
    ]
