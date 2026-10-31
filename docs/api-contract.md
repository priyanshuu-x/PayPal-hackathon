# ProofCheck API contract

Owner: Session A (backend). Session B builds the frontend against this file and mocks any endpoint not yet merged. Change it in the same PR as any endpoint change. Items marked **TBC** are confirmed in the Phase 0 spikes (`docs/architecture.md`).

Base path: `/api`. JSON unless stated. Money is a **decimal string** (`"49.99"`) with a separate `currency`. Timestamps are ISO 8601 UTC (`2026-11-03T10:15:00Z`).

## Conventions

**Errors** (all non-2xx):
```json
{ "error": { "code": "validation_error", "message": "Human-readable, generic", "case_id": "optional" } }
```
Codes: `validation_error` 422, `unauthenticated` 401, `forbidden` 403, `csrf_failed` 403, `not_found` 404, `conflict` 409 (stale `version` or wrong state), `rate_limited` 429, `upload_rejected` 400, `upstream_error` 502, `internal_error` 500. PayPal and Groq error bodies are never returned.

**Merchant auth:** `POST /api/auth/login` sets an HttpOnly, Secure, SameSite=Lax session cookie. Every merchant endpoint needs it. Every `POST` made as the merchant also needs header `X-CSRF-Token` (value from `GET /api/auth/me`). Frontend requests use `credentials: "include"`.

**Buyer (store) endpoints** need no login. The server issues an anonymous buyer-session cookie on the first store call; `GET /api/store/orders` returns only that session's orders.

**Rate limits:** per-IP on store, refund and auth endpoints; `429` with `Retry-After`.

**Text from models** (`reasons`, `draft.text`, Copilot `answer`) is plain text. Render it as plain text only.

## Shared types

```ts
type Verdict = "MATCHES_RECORDS" | "NEEDS_REVIEW" | "DOES_NOT_MATCH_RECORDS";
type CaseStatus = "new" | "processing" | "ready" | "approved" | "declined" | "error";
type CaseSource = "refund" | "dispute";
type Severity = "critical" | "major" | "minor" | "info";

interface CheckResult {
  field: string;                 // "order", "amount", "date", "items", "merchant", "payer", "shipping", "delivery", "embedded_instructions", "reused_file"
  claimed: string | null;
  actual: string | null;
  result: "match" | "mismatch" | "unknown";
  severity: Severity;
  note: string;                  // plain-language reason
}

interface Draft {
  text: string;
  grounded: boolean;
  unsupported_values: string[];
  edited_by_human: boolean;
}

interface CaseSummary {
  id: string;
  source: CaseSource;
  status: CaseStatus;
  verdict: Verdict | null;
  risk_score: number | null;     // 0-100
  amount: string | null;
  currency: string | null;
  paypal_order_id: string | null;
  created_at: string;
  version: number;
}

interface CaseDetail extends CaseSummary {
  capture_id: string | null;
  dispute_id: string | null;
  checks: CheckResult[];
  forensic_notes: string[];
  paypal_record: {               // verified PayPal fields shown on the "PayPal says" side
    order_id: string; amount: string; currency: string; created: string;
    items: { name: string; quantity: number; unit_price: string }[];
    payer_name: string | null; shipping_status: string | null; tracking_number: string | null;
  } | null;
  draft: Draft | null;
  audit: { at: string; actor: string; action: string; hash: string }[];
  audit_chain_valid: boolean;
}
```

## Health

`GET /api/health` -> `200 {"status":"ok"}`

## Auth (merchant)

| Method and path | Body | Response |
|---|---|---|
| `POST /api/auth/login` | `{email, password}` | `200 {merchant_id, csrf_token}` and session cookie |
| `POST /api/auth/logout` | - | `204` |
| `GET /api/auth/me` | - | `200 {merchant_id, csrf_token}` or `401` |

## Demo store (buyer; JS SDK v6)

SDK v6 setup flow (client token vs client ID) is **TBC** in spike 4. The shapes below stay stable either way.

| Method and path | Body | Response |
|---|---|---|
| `GET /api/store/products` | - | `200 [{id, name, price, currency, image}]` |
| `GET /api/store/sdk-config` | - | `200 {client_id, client_token?}` (sandbox, public values only) |
| `POST /api/store/orders` | `{items:[{product_id, quantity}], shipping:{name, address_line_1, admin_area_2, admin_area_1, postal_code, country_code}}` | `200 {order_id}`. **Prices are computed server-side**; the browser's price is ignored |
| `POST /api/store/orders/{order_id}/capture` | - | `200 {order_id, capture_id, status, amount, currency}` |
| `GET /api/store/orders` | - | `200 [{order_id, capture_id, status, amount, currency, created_at, items}]` (this buyer session only) |

## Refund requests (buyer upload)

`POST /api/refunds` (multipart/form-data): `order_id` (string), `reason` (string, max 1000), `email` (optional), `file` (PNG, JPEG, WebP or PDF, max 10 MB).
-> `202 {case_id}`. The file is validated by magic bytes, size and page count. Rejected files return `400 upload_rejected`.

## Cases (merchant)

| Method and path | Notes |
|---|---|
| `GET /api/cases?status=&verdict=&source=&limit=&cursor=` | `200 {items: CaseSummary[], next_cursor}` |
| `GET /api/cases/{id}` | `200 CaseDetail` |
| `GET /api/cases/{id}/evidence` | Returns a re-encoded preview image (private, authenticated; never a public path) |
| `POST /api/cases/{id}/approve` | Body `{action: "approve" | "decline", version: number, draft_text?: string}`. Needs CSRF. `200 CaseDetail` (status `approved`/`declined`, includes refund or evidence reference). `409` on stale `version` or wrong state. This is the **only** endpoint that moves money or sends evidence |

## Case Copilot (merchant, read-only)

| Method and path | Notes |
|---|---|
| `POST /api/cases/{id}/copilot` | Body `{question}` (max 500 chars). Needs CSRF. `200 {answer, tools_called: string[]}`. Max 5 tool calls per question. It cannot approve, refund or change a verdict |
| `GET /api/cases/{id}/copilot` | `200 [{question, answer, tools_called, created_at}]` |

## Webhooks (PayPal -> backend)

`POST /api/webhooks/paypal` - PayPal-signed. Verified with the verify-webhook-signature API; unverified -> `400`; duplicate event ID -> `200` and ignored. Handled: `CUSTOMER.DISPUTE.CREATED`, `CUSTOMER.DISPUTE.UPDATED`, `CUSTOMER.DISPUTE.RESOLVED`. Not called by the frontend.

## Insights and eval (merchant)

| Method and path | Response |
|---|---|
| `GET /api/insights/summary?from=&to=` | `200 {by_verdict: {MATCHES_RECORDS, NEEDS_REVIEW, DOES_NOT_MATCH_RECORDS}, amount_at_risk_caught, currency, merchant_overrides, mismatch_types_by_day: [{date, field, count}]}` - data source for the AG Studio dashboard |
| `GET /api/eval/latest` | `200` contents of `eval/reports/latest.json` |
