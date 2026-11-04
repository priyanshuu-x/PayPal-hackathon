"""Merchant case endpoints (read side). `approve` is added in A7."""

from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlmodel import Session, col, select

from app.api.deps import db_dep, merchant_dep, settings_dep
from app.api.errors import ApiError
from app.api.serializers import case_detail, case_summary
from app.config import Settings
from app.db.models import Case, Evidence
from app.security.auth import MerchantSession
from app.security.uploads import UploadRejected, read_file, reencode_preview

router = APIRouter(prefix="/api/cases", tags=["cases"])


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
    if evidence is None or not evidence.mime.startswith("image/"):
        raise ApiError(404, "not_found", "No preview is available for this case.")
    try:
        preview = reencode_preview(read_file(Path(settings.upload_dir), evidence.storage_key))
    except (FileNotFoundError, UploadRejected) as exc:
        raise ApiError(404, "not_found", "No preview is available for this case.") from exc
    return Response(
        preview,
        media_type="image/png",
        headers={"Cache-Control": "private, no-store", "Content-Disposition": "inline"},
    )
