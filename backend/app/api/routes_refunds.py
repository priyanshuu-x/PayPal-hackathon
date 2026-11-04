"""Buyer refund request: validate the upload, store it privately, open a case.

The case starts as `new`. The analysis pipeline (A6) picks it up. Nothing here talks to PayPal
or to an LLM, and the buyer's reason text is stored but never used as an instruction.
"""

import re
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, Form, UploadFile
from sqlmodel import Session, select

from app.api.deps import DEMO_MERCHANT_ID, buyer_dep, db_dep, dispatcher_dep, settings_dep
from app.api.errors import ApiError
from app.config import Settings
from app.db.models import Case, Evidence, StoreOrder
from app.security.audit import append_event
from app.security.uploads import MAX_BYTES, UploadRejected, store_file, validate_upload
from app.workflows.dispatch import Dispatcher

router = APIRouter(prefix="/api/refunds", tags=["refunds"])

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_OPEN_STATUSES = ("new", "processing", "ready", "approved")


@router.post("", status_code=202)
async def create_refund_request(
    order_id: Annotated[str, Form(min_length=1, max_length=64)],
    reason: Annotated[str, Form(min_length=1, max_length=1000)],
    file: UploadFile,
    buyer_id: Annotated[str, Depends(buyer_dep)],
    db: Annotated[Session, Depends(db_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    dispatcher: Annotated[Dispatcher, Depends(dispatcher_dep)],
    background: BackgroundTasks,
    email: Annotated[str | None, Form(max_length=254)] = None,
) -> dict[str, str]:
    if email and not _EMAIL_RE.fullmatch(email.strip()):
        raise ApiError(422, "validation_error", "Invalid request: email")

    order = db.exec(
        select(StoreOrder).where(
            StoreOrder.paypal_order_id == order_id.strip(), StoreOrder.buyer_id == buyer_id
        )
    ).first()
    if order is None:
        raise ApiError(404, "not_found", "Order not found.")
    if order.status != "captured" or not order.capture_id:
        raise ApiError(409, "conflict", "This order has not been paid yet.")

    existing = db.exec(
        select(Case).where(
            Case.paypal_order_id == order.paypal_order_id,
            Case.source == "refund",
            Case.status.in_(_OPEN_STATUSES),  # type: ignore[attr-defined]
        )
    ).first()
    if existing is not None:
        raise ApiError(409, "conflict", "A refund request for this order is already open.")

    data = await file.read(MAX_BYTES + 1)
    try:
        upload = validate_upload(data)
    except UploadRejected as exc:
        raise ApiError(400, "upload_rejected", str(exc)) from exc

    case = Case(
        id=str(uuid.uuid4()),
        source="refund",
        merchant_id=DEMO_MERCHANT_ID,
        paypal_order_id=order.paypal_order_id,
        capture_id=order.capture_id,
        amount=order.amount,
        currency=order.currency,
        reason=reason.strip(),
        buyer_email=email.strip() if email else None,
    )
    key = store_file(Path(settings.upload_dir), data)
    try:
        db.add(case)
        db.add(
            Evidence(
                case_id=case.id,
                storage_key=key,
                sha256=upload.sha256,
                mime=upload.mime,
                size_bytes=upload.size,
            )
        )
        append_event(
            db,
            case_id=case.id,
            actor="buyer",
            action="case_created",
            details={"source": "refund", "sha256": upload.sha256, "mime": upload.mime},
        )
        db.commit()
    except Exception:
        db.rollback()
        (Path(settings.upload_dir) / key).unlink(missing_ok=True)
        raise
    await dispatcher.enqueue(case.id, background)
    return {"case_id": case.id}
