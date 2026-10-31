# Work split: two Claude Code sessions

Two people (or one person with two sessions) work in parallel. Call them **Session A (Backend and Core)** and **Session B (Frontend and Platform)**. Each session owns a set of folders and never edits the other's folders, so merge conflicts stay rare. The plan is `plan.md`; the rules are `CLAUDE.md`. Both sessions must follow both.

## 1. Ownership

| Area | Owner |
|---|---|
| `backend/app/` (all of it), `backend/tests/` | **A** |
| `Makefile`, `.env.example`, `.github/`, `.pre-commit-config.yaml`, `LICENSE`, `.gitignore` | **A** |
| `frontend/` (store, dashboard, insights, api) | **B** |
| `scripts/`, `eval/`, `postman/`, `render.yaml` | **B** |
| `docs/`, `README.md` | **B** (A adds notes through a PR to B's files, or in `docs/architecture.md` sections it creates) |
| `CLAUDE.md`, `plan.md` | Neither edits alone. Change only by a small PR both approve |

**Contract between them:** `docs/api-contract.md` (endpoints, request and response shapes, error format, auth and CSRF). A writes it in PR A1 and keeps it current. B builds the frontend against it and mocks any endpoint that isn't merged yet. If B needs a change, B opens a PR that edits only `docs/api-contract.md` and tags A.

## 2. Pull request rules (both sessions)

- Branch from the latest `main`. One topic per PR, small, squash merge.
- Branch names: `feat/...`, `fix/...`, `docs/...`, `sec/...`, `chore/...`.
- PR title is a Conventional Commit (`feat: add paypal oauth client`).
- Before opening: `make lint test` passes. After touching `extraction/`, `drafting/`, `verify/`, `copilot/` or any prompt: `make eval-quick`, and paste before/after numbers in the PR body.
- Each PR body has: **What**, **Why**, **How tested**, and the security checklist from `CLAUDE.md` with boxes ticked or marked N/A.
- The other session reviews every PR. Merge only after approval and a green CI.
- Rebase on `main` at the start of each work day.
- **No attribution lines.** Do not add `Co-Authored-By`, "Generated with Claude Code", or any AI name to commits, PR bodies, code comments or docs. Commits carry only the human author.
- Never commit `.env`, keys or tokens. gitleaks must pass. Never bypass hooks.

## 3. Session A: Backend and Core

| PR | When (2026) | Branch | Contents |
|---|---|---|---|
| A1 | Oct 30 | `chore/scaffold` | Repo layout, MIT license, `.gitignore`, `.env.example`, `Makefile`, pre-commit (gitleaks, ruff), CI, `config.py`, `main.py` skeleton, `security/redact.py`, `docs/api-contract.md`. **Merge first; B waits for it before starting** |
| A2 | Oct 31 | `feat/paypal-client` | `paypal/` client (OAuth cache, retries, idempotency), orders, tracking, with respx tests. `routes_store.py` create and capture with server-side amount checks |
| A3 | Nov 1–2 | `feat/uploads-cases-audit` | `security/uploads.py` and tests, DB models, case and evidence endpoints, `security/audit.py` hash chain, `security/auth.py` (session, CSRF), webhook endpoint with signature verification and dedupe |
| A4 | Nov 3 | `feat/verify-engine` | `verify/` (normalise, compare, rules, scoring), table-driven tests, 100% branch coverage |
| A5 | Nov 4 | `feat/extraction-forensics` | `extraction/` (schema, prompts, vision, cache), `forensics/` |
| A6 | Nov 5 | `feat/analyze-case-workflow` | `workflows/analyze_case` with `BackgroundTasks` fallback, daily LLM cap, `routes_cases` read endpoints |
| A7 | Nov 6 | `feat/drafting-approve` | `drafting/` with grounding, templates, `POST /cases/{id}/approve` (refund, idempotent, one audit event) and its tests |
| A8 | Nov 7 | `feat/copilot` | `copilot/` Agent Toolkit with read-only allowlist, startup guard, guard tests, `routes_copilot`, `CopilotTurn` logging |
| A9 | Nov 7 | `feat/disputes-flow-b` | `paypal/disputes.py`, dispute ingest from webhooks, provide-evidence on approve |
| A10 | Nov 8 | `sec/hardening` | CSP, rate limits, CORS allowlist, retention job, security pass against `docs/threat-model.md` |

## 4. Session B: Frontend and Platform

| PR | When (2026) | Branch | Contents |
|---|---|---|---|
| B1 | Oct 30–31 (after A1) | `chore/frontend-boilerplate` | Import `paypaldev/hackathon-paypal-ag-grid-boilerplate` into `frontend/`, strip unused parts, TypeScript strict, `src/api/` skeleton |
| B2 | Oct 31 | `docs/phase0-postman` | `docs/architecture.md` (spike answers as they come), `docs/threat-model.md`, `docs/resources.md` (copy of plan §5), `postman/` collection and environment (variable names only) |
| B3 | Nov 1–2 | `feat/store-checkout-v6` | Demo store, JS SDK v6 buttons, "My orders", refund form with upload. Mock the API until A2 and A3 merge |
| B4 | Nov 2 (after A2) | `feat/seed-sandbox` | `scripts/seed_sandbox.py` (20+ orders with items, shipping, tracking), `scripts/demo_reset.py` |
| B5 | Nov 3–5 | `feat/eval-harness` | `eval/` generator (fictional store only), runner, metrics, report writer, `make eval` and `make eval-quick` wiring (Makefile edit goes through A, or B edits only the eval targets and tags A) |
| B6 | Nov 5–7 | `feat/dashboard-cases` | AG Grid case list (column defs in their own file), case detail with side-by-side table, verdict card, draft editor, Approve and Decline, audit trail. All text rendered as plain text |
| B7 | Nov 8 (after A8) | `feat/copilot-panel` | Copilot chat panel, plain text only |
| B8 | Nov 8 | `feat/insights-ag-studio` | AG Studio dashboard, custom "mismatch types over time" widget, ProofCheck theme, eval report page |
| B9 | Nov 9 | `chore/render-deploy` | `render.yaml` (web service, static site, Postgres, workflows), deploy, set webhook URL in the PayPal dashboard |
| B10 | Nov 10–11 | `docs/readme-demo` | README ("Tools used" copied from plan §5), `docs/demo-script.md`, screenshots, published Postman link |

## 5. Sync points

1. **Oct 30:** A1 merged. Both sessions pull `main`.
2. **Nov 2:** A2 and A3 merged. B switches the store from mocks to the real API.
3. **Nov 5:** verdict endpoints exist. B starts the case detail against real data.
4. **Nov 8:** all A PRs except A10 merged. Integration day; fix contract mismatches in small PRs.
5. **Nov 10:** buffer. Bugs first, then stretch.

## 6. Prompts to give each Claude Code

Paste the matching block as the first message in each session. Both sessions should have `CLAUDE.md` and `plan.md` in the repo root.

### Prompt for Session A (Backend and Core)

```
You are Session A (Backend and Core) on ProofCheck, a two-person effort. Another Claude Code session, Session B (Frontend and Platform), works in parallel.

Read CLAUDE.md, plan.md and docs/work-split.md fully before doing anything. Follow every rule in CLAUDE.md, especially the 12 security invariants.

Ownership: you own backend/, backend/tests/, Makefile, .env.example, .github/, .pre-commit-config.yaml, LICENSE and .gitignore. Do NOT edit frontend/, scripts/, eval/, postman/, render.yaml, docs/ (except docs/api-contract.md, which you own) or README.md. Do not edit CLAUDE.md or plan.md.

Your work is the PR list in docs/work-split.md section 3 (A1 to A10), in order. Start with A1 (chore/scaffold). A1 must include docs/api-contract.md: every endpoint, request and response shape, the error format, and how auth and CSRF work. Session B builds against that file, so keep it current in the same PR as any endpoint change.

Workflow for every PR: branch from the latest main, keep it to one topic, run make lint test (and make eval-quick if you touched extraction, drafting, verify, copilot or a prompt), open a PR with a Conventional Commit title and a body with What, Why, How tested, and the ticked security checklist from CLAUDE.md. Never push to main. Never merge your own PR; Session B reviews.

Hard rules: never add Co-Authored-By lines, "Generated with Claude Code" text, or any AI name to commits, PR bodies, code or docs. Never commit secrets or .env. Never bypass hooks or gitleaks. Sandbox only. If a task would break a security invariant, stop and ask me.

Begin with A1 and tell me before you start each next PR.
```

### Prompt for Session B (Frontend and Platform)

```
You are Session B (Frontend and Platform) on ProofCheck, a two-person effort. Another Claude Code session, Session A (Backend and Core), works in parallel.

Read CLAUDE.md, plan.md and docs/work-split.md fully before doing anything. Follow every rule in CLAUDE.md, especially: render all model and Copilot text as plain text (no dangerouslySetInnerHTML, no Markdown-to-HTML), use PayPal JS SDK v6 (never v5), no analytics or third-party scripts, and no secrets in any file.

Ownership: you own frontend/, scripts/, eval/, postman/, render.yaml, docs/ (except docs/api-contract.md) and README.md. Do NOT edit backend/, Makefile internals beyond the eval and demo targets, .env.example, .github/, CLAUDE.md or plan.md. If you need a backend change or a new env variable, open a PR that edits only docs/api-contract.md (or ask me) and tag Session A.

Your work is the PR list in docs/work-split.md section 4 (B1 to B10). Wait until Session A's PR A1 (scaffold and docs/api-contract.md) is merged to main, then pull and start B1. Build against docs/api-contract.md; mock any endpoint that is not merged yet, and keep all API calls in frontend/src/api/. AG Grid column definitions and the AG Studio theme live in their own files. TypeScript strict.

Workflow for every PR: branch from the latest main, keep it to one topic, run make lint test, open a PR with a Conventional Commit title and a body with What, Why, How tested, and the ticked security checklist from CLAUDE.md. Never push to main. Never merge your own PR; Session A reviews. Also review Session A's PRs when asked, checking them against CLAUDE.md.

Hard rules: never add Co-Authored-By lines, "Generated with Claude Code" text, or any AI name to commits, PR bodies, code or docs. Never commit secrets, .env, license keys or real retailer branding. Never bypass hooks or gitleaks. Sandbox only. If a task would break a security invariant, stop and ask me.

Begin by confirming A1 is merged, then start B1, and tell me before you start each next PR.
```

## 7. PR body template

```
## What

## Why

## How tested
(commands run, results; eval-quick before/after if AI code changed)

## Security checklist
- [ ] No PayPal call outside paypal/ or the Copilot; no money movement outside approve (except buyer checkout)
- [ ] Copilot allowlist unchanged, or still read-only with the guard test passing
- [ ] No raw document text, dispute text or secrets in any prompt
- [ ] Every LLM output parsed by a schema; failure leads to NEEDS_REVIEW
- [ ] No model text rendered as HTML
- [ ] New inputs size-limited, type-checked, rate-limited
- [ ] Logs redacted; new env vars in .env.example (empty); nothing secret in postman/
- [ ] No MCP server in a runtime path
- [ ] plan.md section 5 and README "Tools used" still accurate
```
