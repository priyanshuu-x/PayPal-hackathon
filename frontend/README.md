# ProofCheck frontend

React + Vite + TypeScript (strict). Started from [`paypaldev/hackathon-paypal-ag-grid-boilerplate`](https://github.com/paypaldev/hackathon-paypal-ag-grid-boilerplate); see "What came from the boilerplate" below.

| Path | What |
|---|---|
| `src/store/` | Demo store for sandbox buyers: products, JS SDK v6 checkout, "My orders", refund form (B3) |
| `src/dashboard/` | Merchant app: AG Grid case list, case detail, Copilot panel (B6, B7) |
| `src/insights/` | AG Studio Insights dashboard and theme, eval report page (B8) |
| `src/api/` | **Every** backend call. Types follow `docs/api-contract.md` |
| `src/components/` | Shared UI: app shell, AG Grid wrapper |

## Run

```bash
npm install
npm run dev        # http://localhost:5173, proxies /api to the backend on :8000
npm run lint       # eslint
npm run typecheck  # tsc --noEmit
npm test           # vitest
npm run build
```

`VITE_*` values (`VITE_PAYPAL_CLIENT_ID`, `VITE_AG_STUDIO_LICENSE_KEY`) are read from the repo-root `.env` (see `.env.example`). Anything prefixed `VITE_` ends up in the browser bundle, so only public-by-design values go there.

## Rules that lint enforces

- No `dangerouslySetInnerHTML`, `innerHTML`, `outerHTML` or `insertAdjacentHTML`. Model, Copilot and buyer text renders as plain text (`CLAUDE.md` rule 12).
- No `fetch` outside `src/api/`.

## What came from the boilerplate

The boilerplate is a Next.js app that calls PayPal and OpenAI from its own server. ProofCheck's backend owns every PayPal call (`backend/app/paypal/`) and the frontend is a static site, so only the browser-side parts were kept:

- Kept and adapted: the design tokens (`src/styles/globals.css`), the AG Grid wrapper (`src/components/data-grid.tsx`), the AG Studio theme (`src/insights/studio-theme.ts`), the top-bar layout (`src/components/app-shell.tsx`), and the pinned AG Grid / AG Studio / React versions.
- Removed: the Next.js server and routing, `lib/paypal.ts` (server-side PayPal client), the PayPal endpoint pages (catalog, billing plans, invoices, transaction search, balances), the OpenAI proxy route and AG Studio AI adapter, and the PayPal-data report definitions. The AG Studio dashboard shell (report store, sidebar, dialogs) can be brought back from the boilerplate in B8 if the Insights page needs it.
