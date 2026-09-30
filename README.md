# SpecENGINE-BIS frontend

Interactive manufacturer MVP for standards discovery and compliance planning.

```sh
npm ci
npm run dev -- --webpack
```

Open http://localhost:3000. Demo mode is enabled by default; a backend and LLM key are not needed. Sample catalogue data is bundled locally and case changes persist in this browser. Demo responses are labelled and are not official compliance determinations.

To connect the backend, copy `.env.example` to `.env.local`, set `NEXT_PUBLIC_DEMO_MODE=false`, set `NEXT_PUBLIC_API_URL`, set `NEXT_PUBLIC_RETRIEVAL_API_URL`, and restart/rebuild. Catalogue and Assistant traffic are proxied through Next at `/api/catalogue` and `/api/assistant`, so they remain same-origin when the frontend is opened from a LAN device; both API URLs must be absolute HTTP(S) URLs without a query string or hash. The catalogue service runs at port 8000, while the corpus-backed grounded Assistant (`scraper.assistant_api:app`) runs at port 8001. Demo mode is opt-in: only the exact value `true` enables it, so missing or malformed production configuration remains in live API mode. Failed live requests never fall back silently to demo data. Extended workspace actions need the case API additions documented below.

For production, copy the variable names from `.env.production.example` into the build environment because `NEXT_PUBLIC_*` values are embedded by `next build`. Use `npm run build -- --webpack` followed by `npm start`, or the production multi-stage Dockerfile with all three build arguments. External source actions fail closed unless an HTTPS URL uses an exact authoritative BIS/LIMS hostname.

- `npm run lint` — ESLint
- `npm test` — Stage 4.3 grounded-answer and citation component tests
- `npx tsc --noEmit` — TypeScript
- `npm run build -- --webpack` — production build
- `npm start` — serve the production build

Read [FRONTEND_BACKEND_CONTRACT.md](./FRONTEND_BACKEND_CONTRACT.md) for screen flows, API shapes, existing backend coverage, proposed case endpoints, and implementation order.

## Stage 4.3 citation UX

The Assistant renders only citation IDs included in the validated Stage 4.2
response as interactive evidence. Each unique citation has a compact evidence
card, while its exact authoritative text and available provenance open in the
shared Source Drawer. Unknown citation-like text remains plain text, missing
metadata is omitted, and original-source actions use only the returned URL.
`PARTIAL_EVIDENCE` limitations are displayed verbatim; `NO_VERIFIED_EVIDENCE`
never renders source cards.
