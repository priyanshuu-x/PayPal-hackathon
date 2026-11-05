"""Extraction for one piece of evidence: cache, daily cap, model call, store the result.

The cache key is file SHA-256 + prompt version + model ID, so a prompt or model change never
reuses an old reading. Results are stored on the Evidence row.
"""

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from sqlmodel import Session, select

from app.config import Settings
from app.db.models import Evidence
from app.extraction.images import ImageError, images_for_model
from app.extraction.prompts import EXTRACT_VERSION
from app.extraction.schema import ClaimedFacts
from app.extraction.vision import ExtractionFailed, LimitReached, extract_claims
from app.llm.budget import try_consume
from app.llm.groq_client import GroqClient
from app.security.uploads import read_file

logger = logging.getLogger(__name__)

LIMIT_REASON = "analysis limit reached"
UNREADABLE_REASON = "could not read document"


@dataclass(frozen=True)
class ExtractionOutcome:
    status: Literal["ok", "limit_reached", "failed"]
    facts: ClaimedFacts | None
    model_id: str
    prompt_version: str
    from_cache: bool = False

    @property
    def reason(self) -> str | None:
        """Why the case needs a human, in plain words. None when extraction worked."""
        return {"ok": None, "limit_reached": LIMIT_REASON, "failed": UNREADABLE_REASON}[self.status]


def _cached(db: Session, sha256: str, model: str) -> ClaimedFacts | None:
    row = db.exec(
        select(Evidence).where(
            Evidence.sha256 == sha256,
            Evidence.model_id == model,
            Evidence.extraction_prompt_version == EXTRACT_VERSION,
            Evidence.extracted_json != None,  # noqa: E711  (SQL IS NOT NULL)
        )
    ).first()
    if row is None or row.extracted_json is None:
        return None
    try:
        return ClaimedFacts.model_validate_json(row.extracted_json)
    except ValueError:
        return None  # a stale or damaged cache entry is simply ignored


async def run_extraction(
    db: Session, evidence: Evidence, settings: Settings, client: GroqClient
) -> ExtractionOutcome:
    model = settings.groq_model_vision
    cached = _cached(db, evidence.sha256, model)
    if cached is not None:
        _store(db, evidence, cached, model)
        return ExtractionOutcome("ok", cached, model, EXTRACT_VERSION, from_cache=True)

    try:
        data = read_file(Path(settings.upload_dir), evidence.storage_key)
        images = images_for_model(data, evidence.mime)
    except (FileNotFoundError, ImageError):
        return ExtractionOutcome("failed", None, model, EXTRACT_VERSION)

    try:
        facts = await extract_claims(
            client,
            model=model,
            images=images,
            consume_budget=lambda: try_consume(db, settings.daily_llm_call_cap),
        )
    except LimitReached:
        return ExtractionOutcome("limit_reached", None, model, EXTRACT_VERSION)
    except ExtractionFailed:
        return ExtractionOutcome("failed", None, model, EXTRACT_VERSION)

    _store(db, evidence, facts, model)
    return ExtractionOutcome("ok", facts, model, EXTRACT_VERSION)


def _store(db: Session, evidence: Evidence, facts: ClaimedFacts, model: str) -> None:
    evidence.extracted_json = facts.model_dump_json()
    evidence.extraction_prompt_version = EXTRACT_VERSION
    evidence.model_id = model
    db.add(evidence)
    db.commit()
