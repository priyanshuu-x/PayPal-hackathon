"""HTTP stand-ins for the eval run, plugged into the backend's own clients via httpx transports.

- `RecordedPayPal`: serves the manifest's order snapshots for `GET /v2/checkout/orders/{id}`
  and fails every other call, so an eval run can never write to PayPal or move money.
- `OracleLLM`: answers extraction with the case's ground truth and drafting with a plain note.
  It tests everything around the model (pipeline, verify engine, grounding, metrics) offline;
  its numbers say nothing about model quality and the report labels them as such.
- `CountingTransport`: wraps the real Groq transport and counts extraction and drafting calls.
"""

from __future__ import annotations

import json
from collections import Counter
from typing import Any

import httpx

from dataset import EvalCase


class RecordedPayPal(httpx.AsyncBaseTransport):
    def __init__(self, records: dict[str, dict[str, Any]]) -> None:
        self.records = records
        self.calls: Counter[str] = Counter()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if request.method == "POST" and path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "eval-only", "expires_in": 3600})
        prefix = "/v2/checkout/orders/"
        if request.method == "GET" and path.startswith(prefix) and "/" not in path[len(prefix) :]:
            self.calls["get_order"] += 1
            order_id = path[len(prefix) :]
            if order_id in self.records:
                return httpx.Response(200, json=self.records[order_id])
            return httpx.Response(404, json={"name": "RESOURCE_NOT_FOUND", "debug_id": "eval"})
        self.calls["refused"] += 1
        return httpx.Response(500, json={"name": "EVAL_READ_ONLY", "message": "eval never writes"})


def _kind(request: httpx.Request) -> str:
    try:
        body = json.loads(request.content or b"{}")
    except ValueError:
        return "other"
    return "extract" if "response_format" in body else "draft"


class CountingTransport(httpx.AsyncBaseTransport):
    def __init__(self, inner: httpx.AsyncBaseTransport) -> None:
        self.inner = inner
        self.calls: Counter[str] = Counter()

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.calls[_kind(request)] += 1
        return await self.inner.handle_async_request(request)

    async def aclose(self) -> None:
        await self.inner.aclose()


def oracle_claims(case: EvalCase) -> dict[str, Any]:
    p = case.printed
    return {
        "document_type": "receipt",
        "merchant_name": p.merchant,
        "order_id": p.order_id,
        "transaction_id": None,
        "order_date": p.order_date,
        "currency": p.currency,
        "total_amount": p.total,
        "items": [
            {"name": i.name, "quantity": i.quantity, "unit_price": i.unit_price} for i in p.items
        ],
        "payer_name": p.payer_name,
        "payer_email": None,
        "shipping_address": p.ship_to,
        "visible_damage_description": None,
        "embedded_instructions_detected": p.injection is not None,
        "embedded_instructions_text": p.injection,
        "legibility": "clear",
        "field_confidence": {"order_id": 0.95, "total_amount": 0.95, "order_date": 0.95},
    }


class OracleLLM(httpx.AsyncBaseTransport):
    """Set `current` to the case being analysed before running it."""

    def __init__(self) -> None:
        self.current: EvalCase | None = None

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if _kind(request) == "extract":
            if self.current is None:
                return httpx.Response(500, json={"error": "no current case"})
            content = json.dumps(oracle_claims(self.current))
        else:
            content = (
                "Your refund request has been reviewed and approved. Thank you for your patience."
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
