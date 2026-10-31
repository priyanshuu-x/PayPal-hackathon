# Hackathon resources used

Copy of `plan.md` §5. `plan.md` is the source of truth: when §5 changes, update this file and the README "Tools used" section in the same PR.

Source: the hackathon Resources page and the sponsor pages. Every row here must appear in the README's "Tools used" section with the same wording.

## 5.1 PayPal — core integration
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

## 5.2 PayPal — AI and agent development
| Resource | Used? | How ProofCheck uses it |
|---|---|---|
| **PayPal Agent Toolkit** (`paypal-agent-toolkit`, Agent Toolkit quickstart) | Yes, at runtime | Powers the **Case Copilot** (LangChain + Claude) with a read-only tool allowlist (§7.8) |
| **Agent tools reference** | Yes | Used to choose the read-only allowlist and to confirm that no write tools (e.g. `create_refund`, `accept_dispute_claim`) are enabled |
| **PayPal AI Toolkit on GitHub** (plugin for Claude Code / Codex / Cursor) | Yes, dev time | Installed in Claude Code: `paypal-best-practices` skill, `/paypal:doctor`, `/paypal:explain-error`, `/paypal:sandbox`, `/paypal:test-accounts`, and the pre-write hook that checks SDK v6 best practices |
| **PayPal MCP server** (MCP server quickstart) | Yes, dev time | Connected through the AI Toolkit plugin, so the coding agent can inspect sandbox orders and disputes and create test fixtures. **Not used in the runtime money path** (§10) |
| **PayPal AI docs** (docs.paypal.ai) | Yes | Reference for the Agent Toolkit and MCP setup |

## 5.3 Sponsor tools
| Sponsor | Used? | How ProofCheck uses it |
|---|---|---|
| **Render** | Yes | Hosting (web service, static site, Postgres) using the $50 hackathon credits; **Render Workflows** runs `analyze_case` per case and fans out the eval run across cases |
| **AG Grid / AG Studio** | Yes | Case list grid; **Insights page in AG Studio** with a custom "mismatch types" widget, a ProofCheck theme, verdict-mix and amount-at-risk tiles; frontend started from the **`paypaldev/hackathon-paypal-ag-grid-boilerplate`**; 45-day trial license. Stretch: Studio Agent Framework |
| **APIMatic** | Yes, dev time | **APIMatic Context Plugin for PayPal** installed in Claude Code, so the coding agent pulls accurate PayPal API context while building `paypal/`. Claim the free subscription through the form on the sponsor page |
| **Postman** | Yes | Fork **PayPal's public Postman collection** for the Phase 0 API spikes; publish `postman/ProofCheck.postman_collection.json` (ProofCheck endpoints + the PayPal calls it makes) with an environment file without secrets |
| **Elastic** | Stretch | Near-duplicate evidence search with image embeddings (catches a reused fake photo with small edits) |
| **Zapier** | Stretch | Zapier MCP alert to Slack or email when a case needs review |
| Channel3 | No | Product catalog search for shopping agents; ProofCheck doesn't search products |
| KERNEL | No | Cloud browsers for agents; ProofCheck has no websites to drive |
| Astropods | No | Agent hosting platform; we host on Render, and a second runtime adds risk without benefit |
| Bryntum | No | Scheduling and Gantt UI components; not needed |

## 5.4 Webinars and support
- Watch: *Start building with PayPal* (Oct 6 recording / Oct 13), *Power Your PayPal Hackathon Project with APIMatic Context Plugins* (Oct 7), *Build a payments dashboard without building a dashboard* (Oct 12), which is relevant to the Insights page.
- Support: the PayPal Discord (sponsor channels for AG Grid, APIMatic, Render, Postman).
