"""Disputes v1: read a dispute, find its order, and send evidence.

PROVIDE EVIDENCE SENDS DATA TO PAYPAL on the merchant's behalf. The only caller allowed is the
`POST /cases/{id}/approve` handler (CLAUDE.md rule 4). Accepting a claim is a different PayPal
action that moves money; it is deliberately not implemented anywhere in this repo.

Field names follow the Disputes v1 reference:
https://developer.paypal.com/docs/api/customer-disputes/v1/
The sandbox has no way to open a dispute through the API (a buyer opens it in the UI), so the
evidence call is covered by mocked tests and should be checked against the first real dispute.
"""

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from app.paypal.client import PayPalClient
from app.paypal.models import Dispute

MAX_NOTES_CHARS = 2000
_KNOWN_CARRIERS = {"UPS", "FEDEX", "USPS", "DHL", "DHL_EXPRESS", "ROYAL_MAIL", "CANADA_POST"}


@dataclass(frozen=True)
class EvidenceFile:
    name: str
    content: bytes
    mime: str


@dataclass(frozen=True)
class TrackingInfo:
    tracking_number: str
    carrier: str | None = None


def _id(value: str) -> str:
    return quote(value, safe="")


async def get_dispute(client: PayPalClient, dispute_id: str) -> Dispute:
    body = await client.request("GET", f"/v1/customer/disputes/{_id(dispute_id)}")
    return Dispute.model_validate(body)


async def order_id_for_capture(client: PayPalClient, capture_id: str) -> str | None:
    """The order a capture belongs to, from the capture's `supplementary_data`."""
    body = await client.request("GET", f"/v2/payments/captures/{_id(capture_id)}")
    related = (body.get("supplementary_data") or {}).get("related_ids") or {}
    order_id = related.get("order_id")
    return str(order_id) if order_id else None


def build_evidences(
    notes: str, tracking: list[TrackingInfo], files: list[EvidenceFile]
) -> list[dict[str, Any]]:
    """The `evidences` array: proof of fulfilment when tracking exists, plus any documents."""
    evidences: list[dict[str, Any]] = []
    if tracking:
        infos = []
        for item in tracking:
            carrier = (item.carrier or "").upper().replace(" ", "_")
            if carrier in _KNOWN_CARRIERS:
                infos.append({"carrier_name": carrier, "tracking_number": item.tracking_number})
            else:
                infos.append(
                    {
                        "carrier_name": "OTHER",
                        "carrier_name_other": item.carrier or "Unknown carrier",
                        "tracking_number": item.tracking_number,
                    }
                )
        evidences.append(
            {
                "evidence_type": "PROOF_OF_FULFILLMENT",
                "evidence_info": {"tracking_info": infos},
                "notes": notes[:MAX_NOTES_CHARS],
            }
        )
    other: dict[str, Any] = {"evidence_type": "OTHER", "notes": notes[:MAX_NOTES_CHARS]}
    if files:
        other["evidence_info"] = {"documents": [{"name": f.name} for f in files]}
    if files or not tracking:
        evidences.append(other)
    return evidences


async def provide_evidence(
    client: PayPalClient,
    dispute_id: str,
    *,
    notes: str,
    tracking: list[TrackingInfo],
    files: list[EvidenceFile],
    request_id: str,
) -> dict[str, Any]:
    """POST /v1/customer/disputes/{id}/provide-evidence. JSON when there are no documents,
    multipart (an `input` part plus the files) when there are."""
    if not request_id:
        raise ValueError("request_id is required")
    payload = {"evidences": build_evidences(notes, tracking, files)}
    path = f"/v1/customer/disputes/{_id(dispute_id)}/provide-evidence"
    if not files:
        return await client.request("POST", path, json=payload, request_id=request_id)
    parts: dict[str, tuple[str | None, bytes | str, str]] = {
        "input": (None, json.dumps(payload), "application/json")
    }
    for index, f in enumerate(files, start=1):
        parts[f"file{index}"] = (f.name, f.content, f.mime)
    return await client.request("POST", path, files=parts, request_id=request_id)
