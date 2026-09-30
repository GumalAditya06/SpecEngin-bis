# SpecEngine BIS

An end-to-end BIS standards discovery prototype. This repository contains:

- `./` — Next.js frontend (port 3000)
- `backend/` — catalogue API (port 8000)
- `scraper/` — corpus-backed, cited-answer API (port 8001)

The curated corpus assets needed by the assistant are committed under
`scraper/data/`. Local virtual environments, SQLite databases, downloaded model
caches, and every `.env` file are intentionally excluded from Git.

## Run locally without Docker

Use three terminals. Node.js 22+, Python 3.11+ for `backend`, and Python 3.10+
for `scraper` are required.

### 1. Start the catalogue API

```bash
cd backend
uv sync --group dev
BIS_DATABASE_URL='sqlite+aiosqlite:///./local.db' uv run python -m app.seed
BIS_DATABASE_URL='sqlite+aiosqlite:///./local.db' uv run uvicorn app.main:app --host 127.0.0.1 --port 8000
```

`local.db` is created and seeded locally by the second command; it is not part
of the repository.

### 2. Start the grounded-answer API

```bash
cd scraper
python3 -m venv .venv
.venv/bin/pip install -e '.[embeddings,service]'
cp .env.example .env
# Edit .env and set a newly generated BIS_LLM_API_KEY.
.venv/bin/uvicorn scraper.assistant_api:app --host 127.0.0.1 --port 8001
```

On a new machine, the first start downloads the two retrieval models. After
they have been cached, use `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1` before the
Uvicorn command to run offline. Never commit `.env` or an API key.

### 3. Start the frontend

```bash
cp .env.example .env.local
# In .env.local set NEXT_PUBLIC_DEMO_MODE=false.
npm ci
npm run dev -- --webpack
```

The default URLs in `.env.example` already point to `http://localhost:8000`
and `http://localhost:8001`. Open http://localhost:3000. The Next server
proxies catalogue and assistant requests same-origin, so the UI also works from
a LAN browser when its public URL is configured appropriately.

## Checks

```bash
npm run lint
npm test
npx tsc --noEmit
npm run build -- --webpack
```

Each Python service has its own tests in `backend/tests` and `scraper/tests`.
See `scraper/DEPLOYMENT.md` for production constraints, required model revisions,
and resource sizing.

## Deploy on Render + Vercel

`render.yaml` deploys the catalogue API, its Render Postgres database, and the
grounded-answer API. Create a new Render Blueprint from this GitHub repository,
then supply `BIS_LLM_API_KEY` only in Render's secret prompt. The catalogue
service uses Render's free tier; the assistant is intentionally configured as
`2c-4g`, because the retrieval models need approximately 4 GB RAM. Do not use
the free tier for the assistant.

After Render finishes, copy its two public HTTPS service URLs into Vercel's
**Production** environment variables and redeploy the frontend:

```text
NEXT_PUBLIC_DEMO_MODE=false
NEXT_PUBLIC_API_URL=https://specengin-catalogue.onrender.com
NEXT_PUBLIC_RETRIEVAL_API_URL=https://specengin-assistant.onrender.com
```

Replace the example hostnames with the actual URLs assigned by Render. The
frontend proxies both APIs server-side, so no public browser-to-API CORS rule
is required. Visit `/health/ready` on the assistant once after deployment to
warm the downloaded retrieval models, then test a cited question in the UI.

## Security

If a provider key has ever been pasted into a terminal recording, chat, issue,
or commit, revoke it in the provider console and create a new one. Configure
the replacement only in `scraper/.env` or deployment secrets.
