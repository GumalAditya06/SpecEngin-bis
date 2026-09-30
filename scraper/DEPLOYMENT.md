# Specengine production deployment

This service is platform-neutral. Deploy the backend and frontend as separate
processes behind HTTPS and a reverse proxy. The proxy should enforce its own
request-size and timeout limits in addition to the application boundary.

## Runtime modes

- Development: `SPECENGINE_ENV=development`; localhost CORS is used only when
  `SPECENGINE_CORS_ORIGINS` is absent. Gemini may remain unconfigured.
- Demo: `SPECENGINE_ENV=demo`; intended for explicit local/demo workflows only.
- Production: `SPECENGINE_ENV=production`; set an explicit comma-separated
  HTTPS `SPECENGINE_CORS_ORIGINS`. Wildcards and HTTP origins fail closed.

Install Python 3.10+ dependencies with
`python -m pip install -e '.[embeddings,service]'`. Start the service with:

```bash
uvicorn scraper.assistant_api:app --host 0.0.0.0 --port 8001 --no-access-log
```

The application emits privacy-conscious JSON log events through Python logging.
Configure level, retention and transport in the process supervisor. It does not
log request bodies, complete queries, authorization headers or provider keys.

## Backend environment

| Variable | Required | Purpose |
| --- | --- | --- |
| `SPECENGINE_ENV` | production | Selects strict production configuration. |
| `SPECENGINE_DATA_ROOT` | yes | Absolute root of the mounted, read-only corpus/assets. |
| `SPECENGINE_DATA_DIR` | yes | Data directory; it must resolve inside `SPECENGINE_DATA_ROOT`. |
| `SPECENGINE_CORS_ORIGINS` | yes | Comma-separated HTTPS frontend origins; no wildcard. |
| `SPECENGINE_MAX_REQUEST_BYTES` | no | JSON body cap; default 16,384 and minimum 4,096 bytes. |
| `BIS_LLM_PROVIDER` | for generation | Must remain `gemini` for the frozen provider configuration. |
| `BIS_LLM_MODEL` | for generation | Frozen Gemini model name. |
| `BIS_LLM_API_KEY` | for generation | Secret-managed credential; never bake into an image. |
| `BIS_LLM_API_URL` | no | Existing Gemini endpoint override. |
| `BIS_LLM_TIMEOUT_SECONDS` | no | Existing provider timeout. |
| `HF_HUB_OFFLINE` | recommended | Set to `1` after model assets are preloaded. |
| `TRANSFORMERS_OFFLINE` | recommended | Set to `1` after model assets are preloaded. |

Use `.env.production.example` only as a key list. A missing Gemini credential is
reported as an optional, unconfigured provider by readiness; guarded
`INSUFFICIENT` and `OUT_OF_DOMAIN` queries continue safely without generation.
A query which requires generation receives a controlled 503. No provider
fallback exists.

## Assets and sizing

Mount the existing `data/` tree read-only. Required readiness assets are
`chunks_v2/chunks.jsonl`, `embeddings_v2/embeddings.jsonl`, and all three
vector-store index/config/metadata files under `data/processed/`. Preload the
exact frozen model revisions before enabling offline mode:

- `BAAI/bge-base-en-v1.5` at `a5beb1e3e68b9ab74eb54cfd186867f64f240e1a`
- `cross-encoder/ms-marco-MiniLM-L6-v2` at
  `233902d25c440f23af6f7d6e94d2946bac0bee0a`

Measured on the validation host: model snapshots resolve to approximately
1.22 GB on disk (419 MB embedding model + 805 MB reranker), the data tree is
25 MB, cold retrieval-service initialization is 2,709 ms, the first query is
958 ms, and peak resident memory is 1,100,336 KB. Provision at least 4 GB RAM,
2 CPU cores, and 2 GB free model/data disk for safe operating headroom. GPU is
not required. Warm retrieval remains near the Stage 4.6 baseline (~1,048 ms),
dominated by CPU reranking.

## Health and readiness

- `GET /health/live`: application process is alive; does not load models.
- `GET /health/ready`: validates retrieval assets and initializes the frozen
  retrieval models. Missing Gemini configuration is reported separately but
  does not make the service unready. Asset/model failure returns 503 without
  paths or exception text.

## Frontend

In the frontend project, `NEXT_PUBLIC_*` variables are embedded by `next build`.
Build with `NEXT_PUBLIC_DEMO_MODE=false`, `NEXT_PUBLIC_API_URL`, and
`NEXT_PUBLIC_RETRIEVAL_API_URL`, then run `npm start`. The production Dockerfile
requires those build arguments and runs the Next production server. Demo mode
is enabled only by the exact explicit value `true`; missing or malformed values
select live API mode.

Only HTTPS links on the exact BIS/LIMS host allow-list are rendered as external
source actions: `bis.gov.in`, `www.bis.gov.in`, `standards.bis.gov.in`,
`services.bis.gov.in`, `www.services.bis.gov.in`, and `lims.bis.gov.in`.
