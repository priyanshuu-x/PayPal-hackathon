"""Merchant case endpoints. `approve` is the ONLY code path that moves money (rule 4)."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import update
from sqlmodel import Session, col, select

from app.api.deps import (
    db_dep,
    dispatcher_dep,
    merchant_csrf_dep,
    merchant_dep,
    paypal_dep,
    settings_dep,
)
from app.api.errors import ApiError
from app.api.serializers import case_detail, case_summary
from app.config import Settings
from app.db.models import Case, Draft, Evidence
from app.drafting.draft import facts_from_snapshot
from app.drafting.grounding import MAX_NOTE_CHARS, check_grounding
from app.extraction.images import ImageError, pdf_preview_png
from app.paypal.client import PayPalClient
from app.paypal.errors import PayPalError
from app.paypal.payments import refund_capture
from app.security.audit import append_event
from app.security.auth import MerchantSession
from app.security.uploads import UploadRejected, read_file, reencode_preview
from app.workflows.dispatch import Dispatcher

router = APIRouter(prefix="/api/cases", tags=["cases"])

logger = logging.getLogger(__name__)

STALE_PROCESSING = timedelta(minutes=10)
FAILED_REFUND_STATUSES = frozenset({"FAILED", "CANCELLED"})


def _get_case(db: Session, case_id: str) -> Case:
    case = db.get(Case, case_id)
    if case is None:
        raise ApiError(404, "not_found", "Case not found.")
    return case


@router.get("")
async def list_cases(
    _: Annotated[MerchantSession, Depends(merchant_dep)],
    db: Annotated[Session, Depends(db_dep)],
    status: Literal["new", "processing", "ready", "approved", "declined", "error"] | None = None,
    verdict: Literal["MATCHES_RECORDS", "NEEDS_REVIEW", "DOES_NOT_MATCH_RECORDS"] | None = None,
    source: Literal["refund", "dispute"] | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
    cursor: Annotated[int, Query(ge=0)] = 0,
) -> dict[str, Any]:
    query = select(Case)
    if status:
        query = query.where(Case.status == status)
    if verdict:
        query = query.where(Case.verdict == verdict)
    if source:
        query = query.where(Case.source == source)
    rows = db.exec(
        query.order_by(col(Case.created_at).desc(), col(Case.id)).offset(cursor).limit(limit + 1)
    ).all()
    page = rows[:limit]
    return {
        "items": [case_summary(c) for c in page],
        "next_cursor": str(cursor + limit) if len(rows) > limit else None,
    }


@router.get("/{case_id}")
async def get_case(
    case_id: str,
    _: Annotated[MerchantSession, Depends(merchant_dep)],
    db: Annotated[Session, Depends(db_dep)],
) -> dict[str, Any]:
    return case_detail(db, _get_case(db, case_id))


@router.post("/{case_id}/reanalyze")
async def reanalyze_case(
    case_id: str,
    session: Annotated[MerchantSession, Depends(merchant_csrf_dep)],
    db: Annotated[Session, Depends(db_dep)],
    dispatcher: Annotated[Dispatcher, Depends(dispatcher_dep)],
    background: BackgroundTasks,
) -> dict[str, Any]:
    """Run the analysis again, for example after an error or a temporary PayPal outage.

    It never moves money. Decided cases (approved or declined) are final."""
    case = _get_case(db, case_id)
    if case.status in ("approved", "declined"):
        raise ApiError(409, "conflict", "This case has already been decided.")
    updated = case.updated_at if case.updated_at.tzinfo else case.updated_at.replace(tzinfo=UTC)
    if case.status == "processing" and datetime.now(UTC) - updated < STALE_PROCESSING:
        raise ApiError(409, "conflict", "This case is being analysed right now.")
    case.status = "new"
    case.version += 1
    case.updated_at = datetime.now(UTC)
    db.add(case)
    append_event(
        db,
        case_id=case.id,
        actor=f"merchant:{session.merchant_id}",
        action="reanalysis_requested",
    )
    db.commit()
    await dispatcher.enqueue(case.id, background)
    return case_summary(case)


class ApproveIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: Literal["approve", "decline"]
    version: int = Field(ge=1)
    draft_text: str | None = Field(default=None, max_length=1000)


def _transition(db: Session, case_id: str, version: int, new_status: str) -> bool:
    """Atomically move a `ready` case at exactly this version to `new_status`.
    False means another request got there first, or the version is stale."""
    result = db.execute(
        update(Case)
        .where(col(Case.id) == case_id, col(Case.status) == "ready", col(Case.version) == version)
        .values(status=new_status, version=version + 1, updated_at=datetime.now(UTC))
    )
    return bool(result.rowcount)  # type: ignore[attr-defined]


def _latest_draft(db: Session, case_id: str) -> Draft | None:
    return db.exec(
        select(Draft).where(Draft.case_id == case_id).order_by(col(Draft.id).desc()).limit(1)
    ).first()


def _note_for_refund(db: Session, case: Case, supplied: str | None) -> tuple[str | None, bool]:
    """(note text, edited by a human). An edited note must pass the same grounding check as a
    generated one (rule 6): no number, date or ID that is not in PayPal's record."""
    draft = _latest_draft(db, case.id)
    text = " ".join(supplied.split()) if supplied and supplied.strip() else None
    if text is None or (draft is not None and text == draft.text):
        return (draft.text if draft else None), False
    snapshot = json.loads(case.paypal_record_json) if case.paypal_record_json else None
    facts = facts_from_snapshot(snapshot, case.capture_id) if snapshot else None
    if facts is None:
        raise ApiError(
            422, "validation_error", "There is no PayPal record to check this note against."
        )
    result = check_grounding(text, facts)
    if not result.grounded:
        problems = [
            *result.issues,
            *(f"'{v}' is not in PayPal's record" for v in result.unsupported),
        ]
        raise ApiError(
            422, "validation_error", "The note cannot be sent: " + "; ".join(problems) + "."
        )
    return text, True


@router.post("/{case_id}/approve")
async def approve_case(
    case_id: str,
    body: ApproveIn,
    session: Annotated[MerchantSession, Depends(merchant_csrf_dep)],
    db: Annotated[Session, Depends(db_dep)],
    paypal: Annotated[PayPalClient, Depends(paypal_dep)],
) -> dict[str, Any]:
    """The merchant's decision. Approve refunds the buyer through PayPal; decline only closes
    the case. Needs a merchant session, a CSRF token and the case's current `version`."""
    case = _get_case(db, case_id)
    if case.source != "refund":
        raise ApiError(409, "conflict", "Dispute responses are not available yet.")
    if case.status != "ready":
        raise ApiError(409, "conflict", "This case is not ready for a decision.")
    if case.version != body.version:
        raise ApiError(409, "conflict", "This case changed. Reload it and try again.")
    actor = f"merchant:{session.merchant_id}"

    if body.action == "decline":
        if not _transition(db, case_id, body.version, "declined"):
            db.rollback()
            raise ApiError(409, "conflict", "This case changed. Reload it and try again.")
        append_event(db, case_id=case_id, actor=actor, action="declined")
        db.commit()
        db.refresh(case)
        return case_detail(db, case)

    if not case.capture_id:
        raise ApiError(409, "conflict", "This case has no payment to refund.")
    note, edited = _note_for_refund(db, case, body.draft_text)
    if note and len(note) > MAX_NOTE_CHARS:
        raise ApiError(
            422, "validation_error", f"The note is longer than {MAX_NOTE_CHARS} characters."
        )

    # Claim the case before calling PayPal, so a double click can never refund twice.
    if not _transition(db, case_id, body.version, "processing"):
        db.rollback()
        raise ApiError(409, "conflict", "This case changed. Reload it and try again.")
    db.commit()

    try:
        refund = await refund_capture(
            paypal,
            case.capture_id,
            request_id=f"{case_id}:approve",  # same key every time: PayPal returns the first refund
            note=note,
        )
        if refund.status.upper() in FAILED_REFUND_STATUSES:
            raise PayPalError(f"refund {refund.status}", details={"status": refund.status})
    except PayPalError as exc:
        logger.error("refund_failed", extra={"case_id": case_id, "paypal_debug_id": exc.debug_id})
        db.execute(
            update(Case)
            .where(col(Case.id) == case_id, col(Case.status) == "processing")
            .values(status="ready", version=Case.version + 1, updated_at=datetime.now(UTC))
        )
        append_event(db, case_id=case_id, actor=actor, action="approval_failed")
        db.commit()
        raise ApiError(
            502, "upstream_error", "The refund could not be completed. Nothing was sent. Try again."
        ) from exc

    if note and edited:
        db.add(
            Draft(
                case_id=case_id,
                text=note,
                grounded=True,
                prompt_version="HUMAN",
                edited_by_human=True,
            )
        )
    db.execute(
        update(Case)
        .where(col(Case.id) == case_id)
        .values(
            status="approved",
            refund_id=refund.id,
            version=body.version + 2,
            updated_at=datetime.now(UTC),
        )
    )
    append_event(
        db,
        case_id=case_id,
        actor=actor,
        action="approved",
        details={
            "refund_id": refund.id,
            "refund_status": refund.status,
            "note_sha256": hashlib.sha256((note or "").encode("utf-8")).hexdigest(),
            "edited_by_human": edited,
        },
    )
    db.commit()
    db.refresh(case)
    return case_detail(db, case)


@router.get("/{case_id}/evidence")
async def get_evidence_preview(
    case_id: str,
    _: Annotated[MerchantSession, Depends(merchant_dep)],
    db: Annotated[Session, Depends(db_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> Response:
    """Re-encoded PNG preview (no metadata). Private: needs a merchant session."""
    _get_case(db, case_id)
    evidence = db.exec(
        select(Evidence).where(Evidence.case_id == case_id).order_by(col(Evidence.id))
    ).first()
    if evidence is None:
        raise ApiError(404, "not_found", "No preview is available for this case.")
    try:
        data = read_file(Path(settings.upload_dir), evidence.storage_key)
        preview = (
            pdf_preview_png(data) if evidence.mime == "application/pdf" else reencode_preview(data)
        )
    except (FileNotFoundError, UploadRejected, ImageError) as exc:
        raise ApiError(404, "not_found", "No preview is available for this case.") from exc
    return Response(
        preview,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"},
    )
