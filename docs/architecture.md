# ProofCheck architecture

How the pieces fit, how the two flows run, and what the Phase 0 spikes confirmed about the PayPal sandbox. The product plan is `plan.md` (§4 architecture, §7 components); the API between frontend and backend is `docs/api-contract.md`; the threat model is `docs/threat-model.md`.

> **Status:** spikes 4 and 5 are answered, spike 1 is superseded by design, and spikes 2 and 3 are open (§5). Until a spike is answered, code that depends on it uses the stated fallback.

## 1. Components

| Component | Where | Runs on | Notes |
|---|---|---|---|
| Demo store | `frontend/src/store/` | Render static site | Fictional "Lotus & Loom Demo Store"; PayPal JS SDK v6 buttons; "My orders"; refund form with upload |
| Merchant dashboard | `frontend/src/dashboard/` | Render static site | AG Grid case list, case detail, Copilot panel, Approve / Decline |
| Insights | `frontend/src/insights/` | Render static site | AG Studio dashboard, custom "mismatch types over time" widget, ProofCheck theme, eval report page |
| API | `backend/app/api/` | Render web service | FastAPI; the only thing the browser talks to |
| PayPal client | `backend/app/paypal/` | Render web service | The only REST client for PayPal: OAuth cache, retries, idempotency |
| Analysis pipeline | `extraction/` → `forensics/` → `verify/` → `drafting/` | Render Workflows (`analyze_case`), fallback `BackgroundTasks` | Fixed sequence, not an agent |
| Case Copilot | `backend/app/copilot/` | Render web service | PayPal Agent Toolkit, read-only allowlist |
| Database | `backend/app/db/` | Render Postgres (SQLite locally) | SQLModel |
| Eval | `eval/` | Local or Render Workflows (`run_eval`) | Writes `eval/reports/` |

```
 Buyer ──► Demo store (JS SDK v6) ──► /api/store/* ──► paypal/ ──► Orders v2 (create, capture)
 Buyer ──► Refund form ─────────────► /api/refunds ──► uploads ──► analyze_case ──┐
 PayPal ─► webhook ─────────────────► /api/webhooks/paypal ── verify ─► case ─────┤
                                                                                   ▼
                           extraction (LLM, no tools) → forensics → verify (deterministic) → drafting (LLM + grounding)
                                                                                   │
 Merchant ─► Dashboard ─────────────► /api/cases/* ◄───────────────────────────────┘
          ─► Approve ───────────────► /api/cases/{id}/approve ──► paypal/ ──► refund | provide-evidence
          ─► Copilot ───────────────► /api/cases/{id}/copilot ──► Agent Toolkit (read tools) ──► PayPal reads
```

## 2. Flow A: refund request (primary demo)

1. A sandbox buyer checks out on the demo store. The browser calls `POST /api/store/orders`; the backend builds the order (items, `amount.breakdown`, shipping) with **server-side prices** and calls `POST /v2/checkout/orders`. After the buyer approves in the PayPal popup, the browser calls `POST /api/store/orders/{id}/capture`; the backend captures and stores the capture ID.
2. The buyer opens "My orders" and submits a refund request: order ID, reason and one file (`POST /api/refunds`, multipart). It must come from the same buyer session (cookie) that paid for the order.
3. `security/uploads.py` validates the file (size, magic bytes, pixel and page caps) and stores it privately under a random key. A `Case` (`source=refund`, `status=new`) is created and `analyze_case` is queued. Response: `202 {case_id}`.
4. `analyze_case`: fetch the order by ID (`GET /v2/checkout/orders/{id}`) and tracking → extraction → forensics → verify → verdict → draft with grounding check. Status goes `new` → `processing` → `ready`, usually within seconds (up to about 30 s). Anything that cannot be verified ends as `ready` with `NEEDS_REVIEW`; a crash ends as `error`, and the merchant can call `POST /api/cases/{id}/reanalyze`.
5. The merchant reviews the case and clicks Approve. `POST /api/cases/{id}/approve` checks session, CSRF, `version` and state, calls `POST /v2/payments/captures/{capture_id}/refund` with `PayPal-Request-Id = case_id:approve`, and writes one audit event.

## 3. Flow B: dispute assist

1. A buyer opens a dispute in PayPal's sandbox UI (sandbox disputes can only be opened that way). PayPal sends `CUSTOMER.DISPUTE.CREATED` to `POST /api/webhooks/paypal`.
2. The backend verifies the signature (`POST /v1/notifications/verify-webhook-signature`), dedupes by event ID, fetches the dispute (`GET /v1/customer/disputes/{id}`) and the linked order itself, and creates a `Case` (`source=dispute`). Only the dispute ID from the event is used; buyer-written dispute messages are never read.
3. There is no uploaded document: PayPal's dispute data stands in for the claim. The key check is "not received" against tracking: tracking `delivered` is a major mismatch, so the verdict is `NEEDS_REVIEW` and the draft cites the tracking number. The merchant can attach up to 3 documents (`POST /api/cases/{id}/evidence`).
4. Same pipeline as Flow A. The draft is a dispute response (at most 1500 characters) grounded in PayPal records.
5. On Approve, the backend calls `POST /v1/customer/disputes/{id}/provide-evidence` (multipart) with the tracking details, the approved note and the attached documents, then writes one audit event. It never accepts the buyer's claim.
6. **Demo mode** (`plan.md` §15): with `DEMO_MODE=true`, `POST /api/demo/disputes` creates a demo dispute case (`dispute_id` starts with `DEMO-`) for a paid store order. It runs through the same analysis and approve handler, but approving sends nothing to PayPal. Real webhooks are still verified.

## 4. PayPal sandbox setup (Developer Dashboard)

No values go in this file or anywhere in the repo. Values live in `.env` locally and in the Render dashboard for deploys.

| Item | Where it is set | Env variable |
|---|---|---|
| Sandbox REST app (client ID, secret) | Developer Dashboard → Apps & Credentials → Sandbox | `PAYPAL_CLIENT_ID`, `PAYPAL_CLIENT_SECRET`, `VITE_PAYPAL_CLIENT_ID` |
| Domains allowed to use the JS SDK v6 client token (optional) | — | `PAYPAL_SDK_DOMAINS` |
| Sandbox business account (merchant) | Developer Dashboard → Sandbox → Accounts | — (login listed in the README as a test login) |
| Sandbox personal account (buyer) | Developer Dashboard → Sandbox → Accounts | — (login listed in the README as a test login) |
| Webhook subscription for `CUSTOMER.DISPUTE.CREATED / UPDATED / RESOLVED` | App → Webhooks, URL `https://<render-service>/api/webhooks/paypal` | `PAYPAL_WEBHOOK_ID` |
| Webhook simulator | Developer Dashboard → Testing Tools → Webhooks Simulator | — |

Setup follows `paypaldev/getting-started-with-paypal-sandbox`. Base URL is always `https://api-m.sandbox.paypal.com`.

## 5. Phase 0 spikes

The five spikes from `plan.md` §7.1. Each one records the question, how it was tested, the answer, and what the code does as a result. Postman requests for each spike are in `postman/ProofCheck.postman_collection.json` under "PayPal sandbox".

### Spike 1: dispute attachments readable?

- **Question:** can a sandbox buyer open a dispute with an attachment that our app can read from the dispute details?
- **Why it matters:** decides where Flow B gets its evidence.
- **How to test:** open a dispute as the sandbox buyer on a captured demo order, attach a receipt image, then `GET /v1/customer/disputes/{id}` as the merchant and look for message or evidence entries with document links; try downloading one with the merchant token.
- **Status:** Superseded by design
- **Answer:** not tested. A9 does not read buyer attachments or messages at all (they are hostile input, `CLAUDE.md` rule 5).
- **Decision:** the dispute supplies reason, status, amount and the linked order; the merchant attaches documents on the case page (`POST /api/cases/{id}/evidence`, up to 3).

### Spike 2: webhook simulator reaches Render?

- **Question:** does the Developer Dashboard webhook simulator deliver dispute events to our Render URL, and do simulator events pass `verify-webhook-signature`?
- **Why it matters:** decides how we test Flow B end to end, and whether a simulator event can be used in the demo.
- **How to test:** deploy a build with `POST /api/webhooks/paypal` logging the event ID and verification result; send `CUSTOMER.DISPUTE.CREATED` from the simulator.
- **Status:** Open
- **Answer:** _pending_
- **Decision:** _pending_. **Fallback if simulator events fail verification:** test verification with real sandbox dispute events only; never add a "skip verification" switch.

### Spike 3: sandbox dispute helpers work?

- **Question:** do the sandbox-only dispute state endpoints work for our account (for example moving a dispute to a state where evidence can be provided)?
- **Why it matters:** decides whether Flow B can reach `provide-evidence` in the sandbox, or runs in seeded demo mode.
- **How to test:** create a dispute (spike 1), call the sandbox helper endpoints documented for disputes, then call `provide-evidence` with a test file.
- **Status:** Open (the fallback is already built)
- **Answer:** _pending_. Needs a real sandbox dispute opened in PayPal's buyer UI.
- **Decision:** demo mode (`DEMO_MODE=true`, `POST /api/demo/disputes`) covers the demo if live sandbox disputes can't reach `provide-evidence`; Flow A stays the primary demo.

### Spike 4: JS SDK v6 setup

- **Question:** which v6 setup does the demo store use (client token or client ID flow), and what does the backend need to serve for it?
- **Why it matters:** fixes `GET /api/store/sdk-config` in `docs/api-contract.md` and the store's button code in B3.
- **How to test:** follow the v6 docs and the PayPal AI Toolkit's SDK v6 reference; render a button against the sandbox client ID; complete one create → approve → capture.
- **Status:** Answered by Session A (A2); the browser side is confirmed in B3
- **Answer:** client token flow. The backend requests a browser-safe token (`POST /v1/oauth2/token` with `response_type=client_token`, optional `PAYPAL_SDK_DOMAINS`), caches it about 10 minutes, and serves `{client_id, client_token}` from `GET /api/store/sdk-config`. The browser calls `paypal.createInstance({ clientToken })`.
- **Decision:** order create and capture always go through our backend. Only public values (sandbox client ID, short-lived client token) reach the browser, and v5 patterns are not used.

### Spike 5: Agent Toolkit read-only configuration (Python)

- **Question:** what are the exact Python `paypal-agent-toolkit` configuration keys to enable only the read tools, with sandbox context?
- **Why it matters:** the startup guard in `copilot/toolkit.py` compares real tool names with `READ_ONLY_TOOLS`.
- **How to test:** install the toolkit, configure the actions, print the tool names it exposes, and confirm no write tool appears. Record the keys with a link in `copilot/toolkit.py`.
- **Status:** Answered by Session A (A8), against `paypal-agent-toolkit` 1.11.0
- **Answer:** `Configuration(actions={"orders": {"get": True}, "disputes": {"list": True, "get": True}, "shipment": {"get": True}, "transactions": {"list": True}}, context=Context(sandbox=True))`. The tools are `get_order_details` (not `get_order` as the plan said), `list_disputes`, `get_dispute`, `get_shipment_tracking` and `list_transactions`.
- **Decision:** `READ_ONLY_TOOLS` uses the real names; `assert_read_only` refuses to start on any extra or missing tool.

## 6. Reference links

- PayPal AI docs: https://docs.paypal.ai (Agent Toolkit quickstart, MCP server quickstart, Agent tools reference)
- PayPal REST API reference: https://developer.paypal.com/api/rest/
- PayPal JS SDK v6: https://developer.paypal.com/ (use the v6 docs only)
- Sandbox starter: https://github.com/paypaldev/getting-started-with-paypal-sandbox
- Frontend boilerplate: https://github.com/paypaldev/hackathon-paypal-ag-grid-boilerplate
- Render Workflows: https://render.com/docs
