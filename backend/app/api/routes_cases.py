"""Merchant case endpoints. `approve` is the ONLY code path that moves money or sends
evidence to PayPal (CLAUDE.md rule 4)."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import func, update
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
from app.drafting.draft import NoteKind, facts_from_snapshot, max_chars_for
from app.drafting.grounding import check_grounding
from app.extraction.images import ImageError, pdf_preview_png
from app.paypal.client import PayPalClient
from app.paypal.disputes import EvidenceFile, TrackingInfo, provide_evidence
from app.paypal.errors import PayPalError
from app.paypal.payments import refund_capture
from app.security.audit import append_event
from app.security.auth import MerchantSession
from app.security.uploads import (
    MAX_BYTES,
    UploadRejected,
    read_file,
    reencode_preview,
    store_file,
    validate_upload,
)
from app.workflows.dispatch import Dispatcher

router = APIRouter(prefix="/api/cases", tags=["cases"])

logger = logging.getLogger(__name__)

STALE_PROCESSING = timedelta(minutes=10)
MAX_EVIDENCE_FILES = 3
DEMO_DISPUTE_PREFIX = "DEMO-"
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
    draft_text: str | None = Field(default=None, max_length=2000)


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


def _note_for_case(db: Session, case: Case, supplied: str | None) -> tuple[str | None, bool]:
    """(note text, edited by a human). An edited note must pass the same grounding check as a
    generated one (rule 6): no number, date or ID that is not in PayPal's record."""
    kind: NoteKind = "dispute" if case.source == "dispute" else "refund"
    limit = max_chars_for(kind)
    draft = _latest_draft(db, case.id)
    text = " ".join(supplied.split()) if supplied and supplied.strip() else None
    if text is None or (draft is not None and text == draft.text):
        stored = draft.text if draft else None
        if stored and len(stored) > limit:
            raise ApiError(422, "validation_error", f"The note is longer than {limit} characters.")
        return stored, False
    snapshot = json.loads(case.paypal_record_json) if case.paypal_record_json else None
    facts = facts_from_snapshot(snapshot, case.capture_id) if snapshot else None
    if facts is None:
        raise ApiError(
            422, "validation_error", "There is no PayPal record to check this note against."
        )
    result = check_grounding(text, facts, limit)
    if not result.grounded:
        problems = [
            *result.issues,
            *(f"'{v}' is not in PayPal's record" for v in result.unsupported),
        ]
        raise ApiError(
            422, "validation_error", "The note cannot be sent: " + "; ".join(problems) + "."
        )
    return text, True


_EXTENSIONS = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


def _evidence_files(db: Session, case: Case, settings: Settings) -> list[EvidenceFile]:
    rows = db.exec(
        select(Evidence).where(Evidence.case_id == case.id).order_by(col(Evidence.id))
    ).all()
    files: list[EvidenceFile] = []
    for index, row in enumerate(rows, start=1):
        try:
            content = read_file(Path(settings.upload_dir), row.storage_key)
        except FileNotFoundError as exc:
            raise ApiError(409, "conflict", "An attached file is missing. Add it again.") from exc
        files.append(
            EvidenceFile(f"evidence{index}.{_EXTENSIONS.get(row.mime, 'bin')}", content, row.mime)
        )
    return files


def _require(value: str | None, message: str) -> str:
    if not value:
        raise ApiError(409, "conflict", message)
    return value


def _tracking_from_snapshot(case: Case) -> list[TrackingInfo]:
    snapshot = json.loads(case.paypal_record_json) if case.paypal_record_json else {}
    number = snapshot.get("tracking_number")
    return [TrackingInfo(str(number), snapshot.get("tracking_carrier"))] if number else []


@router.post("/{case_id}/approve")
async def approve_case(
    case_id: str,
    body: ApproveIn,
    session: Annotated[MerchantSession, Depends(merchant_csrf_dep)],
    db: Annotated[Session, Depends(db_dep)],
    paypal: Annotated[PayPalClient, Depends(paypal_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, Any]:
    """The merchant's decision. On a refund case, approve refunds the buyer through PayPal. On a
    dispute case, approve sends the evidence response to PayPal. Decline only closes the case.
    Needs a merchant session, a CSRF token and the case's current `version`."""
    case = _get_case(db, case_id)
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

    is_dispute = case.source == "dispute"
    dispute_id = (
        _require(case.dispute_id, "This case has no dispute to respond to.") if is_dispute else None
    )
    capture_id = (
        None if is_dispute else _require(case.capture_id, "This case has no payment to refund.")
    )
    note, edited = _note_for_case(db, case, body.draft_text)
    files = _evidence_files(db, case, settings) if is_dispute else []

    # Claim the case before calling PayPal, so a double click can never act twice.
    if not _transition(db, case_id, body.version, "processing"):
        db.rollback()
        raise ApiError(409, "conflict", "This case changed. Reload it and try again.")
    db.commit()

    refund_id: str | None = None
    details: dict[str, Any]
    try:
        if dispute_id is not None:
            tracking = _tracking_from_snapshot(case)
            demo = settings.demo_mode and dispute_id.startswith(DEMO_DISPUTE_PREFIX)
            if not demo:  # a demo dispute does not exist at PayPal, so nothing is sent
                await provide_evidence(
                    paypal,
                    dispute_id,
                    notes=note or "Evidence provided by the merchant.",
                    tracking=tracking,
                    files=files,
                    request_id=f"{case_id}:approve",
                )
            details = {
                "evidence_sent": not demo,
                "demo": demo,
                "dispute_id": dispute_id,
                "tracking_included": bool(tracking),
                "files": len(files),
            }
        elif capture_id is not None:
            refund = await refund_capture(
                paypal,
                capture_id,
                request_id=f"{case_id}:approve",  # same key every time: PayPal returns the first
                note=note,
            )
            if refund.status.upper() in FAILED_REFUND_STATUSES:
                raise PayPalError(f"refund {refund.status}", details={"status": refund.status})
            refund_id = refund.id
            details = {"refund_id": refund.id, "refund_status": refund.status}
        else:  # pragma: no cover  (_require guarantees one of the two ids)
            raise ApiError(409, "conflict", "This case cannot be acted on.")
    except PayPalError as exc:
        logger.error("approve_failed", extra={"case_id": case_id, "paypal_debug_id": exc.debug_id})
        db.execute(
            update(Case)
            .where(col(Case.id) == case_id, col(Case.status) == "processing")
            .values(status="ready", version=Case.version + 1, updated_at=datetime.now(UTC))
        )
        append_event(db, case_id=case_id, actor=actor, action="approval_failed")
        db.commit()
        what = "The response" if is_dispute else "The refund"
        raise ApiError(
            502, "upstream_error", f"{what} could not be completed. Nothing was sent. Try again."
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
            refund_id=refund_id,
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
            **details,
            "note_sha256": hashlib.sha256((note or "").encode("utf-8")).hexdigest(),
            "edited_by_human": edited,
        },
    )
    db.commit()
    db.refresh(case)
    return case_detail(db, case)


@router.post("/{case_id}/evidence", status_code=201)
async def add_evidence(
    case_id: str,
    file: UploadFile,
    session: Annotated[MerchantSession, Depends(merchant_csrf_dep)],
    db: Annotated[Session, Depends(db_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> dict[str, int]:
    """Attach a supporting document to a dispute response (for example proof of shipping).
    It is sent to PayPal with the evidence only when the merchant approves."""
    case = _get_case(db, case_id)
    if case.source != "dispute":
        raise ApiError(409, "conflict", "Documents can only be added to dispute cases.")
    if case.status in ("approved", "declined"):
        raise ApiError(409, "conflict", "This case has already been decided.")
    existing = db.exec(
        select(func.count()).select_from(Evidence).where(Evidence.case_id == case_id)
    ).one()
    if existing >= MAX_EVIDENCE_FILES:
        raise ApiError(409, "conflict", f"At most {MAX_EVIDENCE_FILES} documents can be attached.")
    data = await file.read(MAX_BYTES + 1)
    try:
        upload = validate_upload(data)
    except UploadRejected as exc:
        raise ApiError(400, "upload_rejected", str(exc)) from exc
    key = store_file(Path(settings.upload_dir), data)
    try:
        db.add(
            Evidence(
                case_id=case_id,
                storage_key=key,
                sha256=upload.sha256,
                mime=upload.mime,
                size_bytes=upload.size,
            )
        )
        append_event(
            db,
            case_id=case_id,
            actor=f"merchant:{session.merchant_id}",
            action="evidence_added",
            details={"sha256": upload.sha256, "mime": upload.mime},
        )
        db.commit()
    except Exception:
        db.rollback()
        (Path(settings.upload_dir) / key).unlink(missing_ok=True)
        raise
    return {"evidence_count": existing + 1}


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
