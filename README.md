# ProofCheck

**AI that checks refund and dispute evidence against PayPal's real records.**

A fake receipt can look perfect, but it cannot match a record it doesn't control. ProofCheck reads what a receipt, screenshot or photo claims, compares it with the order PayPal actually holds, and shows the merchant where they differ. A person approves every action.

> *The AI reads and explains. The PayPal records decide. A human approves.*

Built for the PayPal AI Hackathon. **Sandbox only:** no real money or customer data.

- **Demo:** https://proofcheck-web.onrender.com (sandbox only; the first request after idle can take about a minute while the free-plan server wakes up)
- **Video:** `VIDEO LINK: TO CONFIRM`
- **Postman collection:** https://documenter.getpostman.com/view/57305428/2sBYHYuHhQ ([files in this repo](postman/))
- **License:** [MIT](LICENSE)

## The problem

Refund and dispute processes often trust the document a customer uploads instead of the transaction record, and generative AI has made fake documents cheap and convincing. Small online merchants who handle refunds by hand, with no fraud team, are the ones who pay for it.

## What it does

1. **Takes the evidence:** a refund request on the merchant's store, or a PayPal dispute through a verified webhook.
2. **Pulls the real record** from PayPal: order, capture, amount, items, payer, tracking.
3. **Reads the document:** a vision model extracts what it claims into a fixed schema. It transcribes; it does not judge.
4. **Adds forensic signals:** metadata and editing traces, as weak context only.
5. **Compares deterministically:** code, not a model, checks the claims against the record and produces one of three verdicts with reasons: `MATCHES_RECORDS`, `NEEDS_REVIEW` or `DOES_NOT_MATCH_RECORDS`.
6. **Drafts a reply** that is checked so every number, date and ID exists in PayPal's record.
7. **Waits for the merchant.** Only their Approve click refunds the buyer or sends evidence to PayPal.

Also included:

- **Case Copilot:** a read-only assistant on the case page, built on the PayPal Agent Toolkit. It answers from live PayPal data and cannot move money.
- **Insights:** an AG Studio dashboard with the verdict mix, amount at risk, and a custom "mismatch types over time" widget.
- **Demo store:** the fictional Lotus & Loom Demo Store, with PayPal JS SDK v6 checkout, so demo orders are real sandbox orders.

## Try it

Sandbox logins for judges:

| Role | Where | Login |
|---|---|---|
| Buyer | PayPal popup at checkout | Email `sb-kew9653687070@personal.example.com`, password `^z8OEb-z` (a PayPal sandbox **personal** account, test money only) |
| Merchant | https://proofcheck-web.onrender.com/merchant | Email `merchant@proofcheck.demo`, password `merchant07` |

1. Open the store, add something to the cart and pay with the sandbox buyer.
2. In **My orders**, request a refund and attach a receipt image that shows the order ID, date, items and total.
3. Sign in at `/merchant` and open the case: compare the document with PayPal's record, ask the Copilot about it, and approve or decline.

Known limits of this hosted demo:

- It runs on free hosting plans. The server sleeps when idle, uploaded evidence files are kept only until the next restart (cases, verdicts and the audit log stay in the database), and the free database is deleted about 30 days after creation.
- The AI provider's free tier has a daily token limit. When it is used up, new documents get the verdict `NEEDS_REVIEW` with the reason "analysis limit reached".
- Signed PayPal dispute webhooks were not exercised end to end in the sandbox. Unsigned events are rejected, and disputes can be demonstrated through demo mode, which creates a dispute case for a paid order and sends nothing to PayPal.
- The AG Studio Insights page runs without a license key, so it shows a watermark.

## Architecture

```
Sandbox buyer ──► Demo store (React, JS SDK v6) ──► FastAPI backend ──► PayPal sandbox REST
PayPal webhook ─────────────────────────────────►   │  (paypal/ is the only REST client)
                                                    ▼
             extraction (vision LLM, no tools) → forensics → verify (deterministic) → drafting (LLM + grounding)
                                                    │
Merchant ◄──► Dashboard (AG Grid) · Copilot (Agent Toolkit, read tools only) · Insights (AG Studio)
                                                    │
                     POST /cases/{id}/approve: the only path that refunds or sends evidence
```

More detail: [architecture](docs/architecture.md), [API contract](docs/api-contract.md), [threat model](docs/threat-model.md), [deployment](docs/deploy.md).

## Security

Uploaded documents and buyer-written messages are treated as hostile input.

- **The LLM never decides.** Output is parsed by a schema, and any failure leads to `NEEDS_REVIEW`, never a pass.
- **Extraction and drafting have no tools and no credentials.** Text hidden in a receipt has nothing to act on, and it is flagged as a critical check.
- **The only agent can read but never write.** The Copilot's tools are a fixed allowlist, and the app refuses to start if any other tool appears.
- **Money moves through one audited path:** a merchant session, a CSRF token, the current case version, a `PayPal-Request-Id` idempotency key and one audit event.
- **Tamper-evident history:** the audit log is a hash chain, shown on each case as "Log verified".
- **Drafts are grounded:** a deterministic check rejects any number, date or ID that isn't in PayPal's record.
- **Webhooks are verified** with PayPal's signature API and deduplicated by event ID.
- **No auto-denial:** verdict names are neutral, reasons are shown, and the merchant decides.

The record comparison carries the result. AI-image detection is unreliable, so forensic signals only add context and never decide a verdict on their own.

## Evaluation

The pipeline is measured on labelled receipts for the fictional store: genuine, degraded, tampered, fabricated, prompt-injection and reused-file documents, plus Copilot questions that include attempts to make it refund. 20% of cases are held out and never used for tuning. Method: [eval/README.md](eval/README.md).

**Results** from a real run against the live models (`qwen/qwen3.8-27b` for extraction, `openai/gpt-oss-120b` for drafting). It used a stratified sample of 100 of the 140 cases, so every bucket is represented and 28 cases are held out; the full dataset was not run because of the free-tier daily token limit on the AI provider. Full report: [eval/reports/latest.md](eval/reports/latest.md).

| Metric | Target | Result |
|---|---|---|
| Verdict accuracy (all 100 cases) | n/a | 96% |
| False-alarm rate on genuine documents | ≤ 2% | **4.7%** (2 of 43): target missed |
| Key-field extraction accuracy (clear images) | ≥ 95% | 96.5% |
| Mismatch recall (critical fields) | ≥ 95% | 100% |
| Prompt injection: not followed / reported | 100% / ≥ 90% | 100% / 100% |
| Grounded drafts, first try | ≥ 95% | 100% |
| Copilot write attempts executed | 0 | Not part of this run; writes are blocked by the read-only tool allowlist and its startup guard |
| p95 latency | ≤ 15 s | **51 s**: target missed (p50 24 s) |

Known weaknesses: all of the false alarms come from the vision model misreading the long random order ID (a dropped or added character, or 0 read as O), which makes the exact ID comparison fail. Six genuine documents were affected: two became a hard mismatch and four went to human review. Latency is dominated by the AI provider's free-tier throttling.

## Run it locally

Needs Python 3.11, [uv](https://docs.astral.sh/uv/), Node 20.19+, a PayPal sandbox REST app and a Groq API key.

```bash
cp .env.example .env     # fill in the sandbox and Groq values
make setup
make dev                 # backend on :8000
```

In a second terminal:

```bash
cd frontend && npm run dev   # http://localhost:5173
```

Set `COOKIE_SECURE=false` in `.env` for local http. Create the merchant password hash with `python -m app.security.auth`, run inside `backend/`.

Other commands: `make lint test`, `make seed` (sandbox orders), `make eval-quick`, `make eval`, `make demo-reset`.

## Tools used

### PayPal — core integration
| Resource | Used? | How ProofCheck uses it |
|---|---|---|
| **PayPal Developer Dashboard** | Yes | Sandbox app (client ID and secret), sandbox business (merchant) and personal (buyer) accounts, webhook subscription for dispute events, **webhook simulator** for testing |
| **Getting Started with PayPal sandbox** (`paypaldev/getting-started-with-paypal-sandbox`) | Yes | Reference for sandbox account setup and auth; `scripts/seed_sandbox.py` follows its patterns |
| **PayPal Developer docs / REST API** | Yes | All server calls: OAuth, Orders v2, Payments v2 refunds, Disputes v1, order tracking, webhook signature verification (§7.1) |
| **PayPal Checkout / Payments** | Yes | The demo store checkout flow (create order → buyer approves → capture), which produces the real records ProofCheck checks against |
| **PayPal JavaScript SDK v6** | Yes | PayPal buttons on the demo store's checkout page; the backend creates and captures orders |
| **Disputes** | Yes | Flow B: read dispute details, submit evidence after approval; dispute webhooks |
| **Webhooks** | Yes | `CUSTOMER.DISPUTE.CREATED / UPDATED / RESOLVED`, verified with the verify-webhook-signature API |
| **Invoicing** | Stretch | InvoiceShield: create test scam-style invoices between sandbox accounts |
| Pay Later, Subscriptions, Payouts, Donations, Low-code payment links | No | Not related to verifying refund or dispute evidence; using them only to tick a box would weaken the design score |
| APIMatic Context Plugin for PayPal | No | Not used while building this project, so it is not claimed; PayPal API details came from the official docs and the Postman collection |
| PayPal AI Toolkit plugin and PayPal MCP server (for Claude Code) | No | Not used while building this project, so they are not claimed. The runtime Case Copilot does use the PayPal Agent Toolkit (listed above), and no MCP server is ever in the runtime path |

### PayPal — AI and agent development
| Resource | Used? | How ProofCheck uses it |
|---|---|---|
| **PayPal Agent Toolkit** (`paypal-agent-toolkit`, Agent Toolkit quickstart) | Yes, at runtime | Powers the **Case Copilot** (LangChain + Groq-hosted `openai/gpt-oss-120b`) with a read-only tool allowlist (§7.8) |
| **Agent tools reference** | Yes | Used to choose the read-only allowlist and to confirm that no write tools (e.g. `create_refund`, `accept_dispute_claim`) are enabled |
| **PayPal AI docs** (docs.paypal.ai) | Yes | Reference for the Agent Toolkit and MCP setup |

### Sponsor tools
| Sponsor | Used? | How ProofCheck uses it |
|---|---|---|
| **Render** | Yes | Hosting (web service, static site, Postgres) on Render's free plans (the API sleeps when idle and keeps uploads only temporarily); Render Workflows task code is included (`backend/app/workflows/`), but it is **off in this deployment**: analysis runs in the API process, because a workflow task cannot read the API's local upload folder |
| **AG Grid / AG Studio** | Yes | Case list grid; **Insights page in AG Studio** with a custom "mismatch types" widget, a ProofCheck theme, verdict-mix and amount-at-risk tiles; frontend started from the **`paypaldev/hackathon-paypal-ag-grid-boilerplate`**; 45-day trial license. Stretch: Studio Agent Framework |
| **Postman** | Yes | Fork **PayPal's public Postman collection** for the Phase 0 API spikes; publish `postman/ProofCheck.postman_collection.json` (ProofCheck endpoints + the PayPal calls it makes) with an environment file without secrets |
| **Elastic** | Stretch | Near-duplicate evidence search with image embeddings (catches a reused fake photo with small edits) |
| **Zapier** | Stretch | Zapier MCP alert to Slack or email when a case needs review |
| Channel3 | No | Product catalog search for shopping agents; ProofCheck doesn't search products |
| KERNEL | No | Cloud browsers for agents; ProofCheck has no websites to drive |
| Astropods | No | Agent hosting platform; we host on Render, and a second runtime adds risk without benefit |
| Bryntum | No | Scheduling and Gantt UI components; not needed |

Groq-hosted models run extraction, drafting and the Copilot. Model IDs are in [`.env.example`](.env.example).

## Project layout

| Path | What |
|---|---|
| `backend/app/` | FastAPI app: `paypal/`, `extraction/`, `forensics/`, `verify/`, `drafting/`, `copilot/`, `workflows/`, `security/` |
| `frontend/` | React + Vite: `src/store/`, `src/dashboard/`, `src/insights/`, `src/api/` |
| `eval/` | Dataset generator, runner, metrics, reports |
| `scripts/` | `seed_sandbox.py`, `demo_reset.py` |
| `postman/` | Collection and environment (variable names only) |
| `docs/` | Architecture, API contract, threat model, deployment, demo script, resources |

## License

[MIT](LICENSE)
