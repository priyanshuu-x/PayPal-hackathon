"""The published eval report, written by eval/report.py. Oracle and quick runs go to other files,
so this never serves numbers that did not come from the real model on the full set."""

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends

from app.api.deps import merchant_dep
from app.api.errors import ApiError
from app.security.auth import MerchantSession

router = APIRouter(prefix="/api/eval", tags=["eval"])

LATEST_REPORT = Path(__file__).resolve().parents[3] / "eval" / "reports" / "latest.json"


@router.get("/latest")
async def latest_eval(_: Annotated[MerchantSession, Depends(merchant_dep)]) -> dict[str, Any]:
    try:
        report: dict[str, Any] = json.loads(LATEST_REPORT.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        raise ApiError(404, "not_found", "No eval report has been published yet.") from None
    return report
