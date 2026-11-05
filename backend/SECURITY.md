# Backend security review (A10)

A pass over `docs/threat-model.md` (threats T1 to T22) against the code, with the test that proves
each control and the gaps that remain. Update this file when a control moves. The 12 rules in
`CLAUDE.md` are the invariants; this file says where each one is enforced.

## Control map

| Threat | Control in code | Proof |
|---|---|---|
| T1 injection via evidence | `extraction/prompts.py` (document is data), `extraction/schema.py`, `verify/compare.py` flags embedded instructions as critical and never copies their text | `test_extraction` (injection reported), `test_pipeline::test_injection_text_is_flagged_and_never_followed` |
| T2 tool misuse | `copilot/toolkit.py` allowlist + `assert_read_only` at startup; `CaseCopilot` binds only allowlisted tools and refuses any other name | `test_copilot` (guard, widened toolkit, app refuses to boot, zero writes reach PayPal) |
| T3 injection via dispute messages | `paypal/models.py::Dispute` has no `messages` field; Copilot has no write tools; its answer is cleaned to plain text | `test_disputes::test_get_dispute_reads_only_structured_fields`, `test_copilot` |
| T4 fabricated replies | `drafting/grounding.py` | `test_grounding` (100% branch coverage) |
| T5 LLM output as a decision | `verify/` decides; every model output is parsed by a schema; failure gives `NEEDS_REVIEW` | `test_verify_*` (100%), `test_extraction` failure paths |
| T6 forged webhooks | `paypal/webhooks.py` verifies first; dedupe by event ID; a dispute that cannot be fetched is not recorded | `test_webhooks`, `test_disputes` |
| T7 double execution | claim-first atomic transition, `PayPal-Request-Id = <case>:approve`, version lock | `test_approve` (stale, replay, atomic claim, retry reuses key) |
| T8 unauthorised money movement | `security/auth.py`, `merchant_csrf_dep`; the only caller of `refund_capture` and `provide_evidence` is `approve_case` | `test_approve`, `test_disputes` source scans, `test_hardening` route inventory |
| T9 price tampering | server-side catalog, unknown fields rejected, totals re-checked at capture | `test_store_routes` |
| T10 credential leakage | `.env` ignored, `security/redact.py`, gitleaks in CI and pre-commit, errors never include upstream bodies | `test_redact`, gitleaks job |
| T11 secrets in prompts | prompts built only from schema fields and record fields | `test_extraction`, `test_draft`, `test_copilot` (context keys) |
| T13 malicious uploads | `security/uploads.py`; request size cap in `middleware.py`; files stored under random keys; previews re-encoded | `test_uploads`, `test_hardening` (413) |
| T14 XSS | JSON-only API with `default-src 'none'` CSP; Copilot answers stripped of control characters and Markdown emphasis; the frontend renders text only (B6, B7) | `test_hardening` (headers), `test_copilot::test_unicode_spaces...` |
| T15 PII | `security/retention.py` (daily, 30 days), redacted logs, authenticated previews | `test_hardening` (retention), `test_redact` |
| T16 unauthorised access | every route outside a short public list needs a session; every merchant POST needs CSRF | `test_hardening::test_every_route_outside_the_public_list_needs_a_merchant_session` and the CSRF twin, which read the OpenAPI schema so a new route cannot slip through |
| T17 denial of wallet | per-IP limits (`security/ratelimit.py`), daily model-call cap, extraction cache | `test_hardening` (429, Retry-After, spoofed header, memory bound), `test_groq_client`, `test_pipeline` |
| T18 supply chain | exact pins; CI `audit` job (`pip-audit`, blocking); Dependabot; PayPal's toolkit installed alone (see below) | CI `audit` job |
| T19 history tampering | hash-chained audit log, unique `prev_hash` | `test_audit` |
| T20 harm to honest customers | no auto-deny, grounded neutral wording, banned accusing terms | `test_grounding`, `test_verify_*` |
| T21 live-mode misconfiguration | `PAYPAL_BASE_URL` validated, toolkit built with `sandbox=True`, checked again before building | `test_config_and_health`, `test_copilot` |
| T22 lagging data | orders looked up by ID; missing data is `unknown`, outage is `NEEDS_REVIEW` | `test_verify_compare`, `test_pipeline` |

## Findings fixed in this pass

1. **Vulnerable dependencies on the upload path.** `pip-audit` reported 16 vulnerable packages,
   including Pillow 11.1 (69 advisories), pypdf 5.1 (103), python-multipart 0.0.20 (12) and
   Starlette 0.41 (14): the libraries that parse hostile files and requests. Upgraded to Pillow
   13.0.0, pypdf 6.21.4, python-multipart 0.0.33, Starlette 1.10.0 (FastAPI 0.162.0) and pytest 9.
   `pip-audit` now reports none.
2. **PayPal's Agent Toolkit dragged in ~140 unused packages** (crewai-tools, chromadb, mem0ai,
   embedchain, openai-agents ...) for integrations we do not use. They carried advisories with no
   fix and capped pypdf and langchain-core at vulnerable versions. The toolkit is now installed on
   its own (`requirements-paypal-toolkit.txt`, `uv pip install --no-deps`) with LangChain 1.x
   pinned in `pyproject.toml`. The backend went from ~190 packages to 95.
3. **No request size limit before parsing.** A multipart body is spooled before a handler runs, so
   an oversized upload could fill memory or disk. `BodySizeLimitMiddleware` refuses by
   `Content-Length` and by counting streamed bytes.
4. **No rate limits** (only the login and Copilot throttles). Added per-IP limits for every route
   group; the client IP honours `X-Forwarded-For` only for `TRUSTED_PROXY_COUNT` proxies.
5. **No security headers or CSP** beyond three basics; Swagger and ReDoc pages were exposed.
6. **No retention job.** Uploaded files and personal data were kept forever.
7. **Header middleware could have dropped repeated headers** (several `Set-Cookie` lines) if it
   copied them into a dictionary; the rewrite keeps them, with a test.

## Known gaps and residual risks

- **Rate limiter and login throttle are in memory, per instance.** Correct for the single
  Render web service we deploy; with several instances each would count on its own.
- **Sessions are stateless signed cookies** (8 hours). Logging out clears the browser's copy but a
  stolen cookie stays valid until it expires. Changing `SESSION_SECRET` revokes all sessions.
- **Dependencies are pinned to exact versions, not hash-pinned.** CI audits them and Dependabot
  proposes updates; hash pinning (`uv pip compile --generate-hashes`) is a possible next step.
- **The Agent Toolkit uses synchronous `requests` with no timeout.** Each Copilot lookup runs in a
  thread with a 20 second cut-off; a hung request can leave a thread behind until it finishes.
- **The toolkit is installed without its declared dependencies.** If a future version needs
  something new, importing it fails and the app refuses to start (it does not run without the
  guard).
- **`provide-evidence` is unverified against a live dispute.** The request format follows the
  Disputes v1 docs; the sandbox cannot create a dispute through the API. Failures are safe
  (case returns to `ready`, retry is idempotent).
- **`DEMO_MODE` must stay off outside demos.** It only adds a merchant-authenticated endpoint that
  creates a marked demo dispute and makes approving it skip PayPal; real webhooks are unaffected.
- **No database migrations.** Schema changes need a fresh database (SQLite locally, new Postgres
  on Render) until Alembic is added.
- **The frontend's own CSP** is set by the static site (B9), not this API. It must allow the
  PayPal JS SDK v6 origins and the AG Studio license endpoint, and nothing else; test it in the
  browser before the demo.
- **Login has no CSRF token** (nothing to protect before a session exists); it is rate limited and
  locks per email and IP.

## Running the checks locally

```bash
make lint test                       # includes the 100% coverage gate on the security-critical modules
uv pip freeze --python backend/.venv/Scripts/python.exe | grep -v "^-e" > /tmp/req.txt
pip-audit -r /tmp/req.txt --no-deps --disable-pip      # known vulnerabilities in dependencies
```
