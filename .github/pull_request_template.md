## What

## Why

## How tested
(commands run and results; eval-quick before/after if AI code changed)

## Security checklist
- [ ] No PayPal call outside `paypal/` or the Copilot; no money movement outside `approve` (except buyer checkout)
- [ ] Copilot allowlist unchanged, or still read-only with the guard test passing
- [ ] No raw document text, dispute text or secrets in any prompt
- [ ] Every LLM output parsed by a schema; failure leads to NEEDS_REVIEW
- [ ] No model text rendered as HTML
- [ ] New inputs size-limited, type-checked, rate-limited
- [ ] Logs redacted; new env vars in `.env.example` (empty); nothing secret in `postman/`
- [ ] No MCP server in a runtime path
- [ ] `plan.md` section 5 and README "Tools used" still accurate
