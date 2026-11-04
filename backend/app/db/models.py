"""SQLModel tables. Raw uploads are never stored here (only a private storage key and a hash)."""

from datetime import UTC, datetime

from sqlmodel import Field, SQLModel


def utcnow() -> datetime:
    return datetime.now(UTC)


def utcnow_iso() -> str:
    return utcnow().isoformat(timespec="microseconds").replace("+00:00", "Z")


class StoreOrder(SQLModel, table=True):
    """A demo-store order, owned by an anonymous buyer session.

    Amounts are decimal strings; the totals here are computed server-side and are what capture
    is checked against.
    """

    id: int | None = Field(default=None, primary_key=True)
    buyer_id: str = Field(index=True)
    paypal_order_id: str = Field(unique=True, index=True)
    status: str = "created"  # created | captured
    currency: str
    amount: str
    items_json: str
    capture_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Case(SQLModel, table=True):
    id: str = Field(primary_key=True)  # uuid4
    source: str  # refund | dispute
    merchant_id: str = Field(index=True)
    paypal_order_id: str | None = Field(default=None, index=True)
    capture_id: str | None = None
    dispute_id: str | None = Field(default=None, index=True)
    status: str = Field(default="new", index=True)  # new|processing|ready|approved|declined|error
    verdict: str | None = Field(default=None, index=True)
    risk_score: int | None = None
    amount: str | None = None
    currency: str | None = None
    # Buyer-written text: hostile input. Never placed in a prompt (CLAUDE.md rule 5).
    reason: str | None = None
    buyer_email: str | None = None
    version: int = 1
    created_at: datetime = Field(default_factory=utcnow, index=True)
    updated_at: datetime = Field(default_factory=utcnow)


class Evidence(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: str = Field(foreign_key="case.id", index=True)
    storage_key: str  # random key of a private file, never a public path
    sha256: str = Field(index=True)
    mime: str
    size_bytes: int
    extracted_json: str | None = None
    extraction_prompt_version: str | None = None
    model_id: str | None = None
    forensics_json: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class CheckResult(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: str = Field(foreign_key="case.id", index=True)
    field: str
    claimed: str | None = None
    actual: str | None = None
    result: str  # match | mismatch | unknown
    severity: str  # critical | major | minor | info
    note: str = ""


class Draft(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: str = Field(foreign_key="case.id", index=True)
    text: str
    grounded: bool = False
    unsupported_values_json: str = "[]"
    prompt_version: str | None = None
    model_id: str | None = None
    edited_by_human: bool = False
    created_at: datetime = Field(default_factory=utcnow)


class CopilotTurn(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    case_id: str = Field(foreign_key="case.id", index=True)
    question: str
    tools_called_json: str = "[]"
    answer: str
    prompt_version: str | None = None
    model_id: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class AuditEvent(SQLModel, table=True):
    """Hash-chained log: hash = sha256(prev_hash + canonical payload). See security/audit.py."""

    id: int | None = Field(default=None, primary_key=True)
    case_id: str | None = Field(default=None, index=True)
    actor: str  # system | buyer | merchant:<id> | paypal
    action: str
    details_json: str = "{}"
    created_at: str  # ISO 8601 UTC text, so the hash input never changes after a DB round trip
    # Unique, so two writers can never extend the chain from the same event.
    prev_hash: str = Field(unique=True)
    hash: str = Field(unique=True)


class LlmUsage(SQLModel, table=True):
    """Model calls made per UTC day, for DAILY_LLM_CALL_CAP."""

    day: str = Field(primary_key=True)  # YYYY-MM-DD
    calls: int = 0


class WebhookEvent(SQLModel, table=True):
    """Dedupe table: one row per PayPal webhook event ID we have accepted."""

    id: int | None = Field(default=None, primary_key=True)
    event_id: str = Field(unique=True, index=True)
    event_type: str
    received_at: datetime = Field(default_factory=utcnow)
