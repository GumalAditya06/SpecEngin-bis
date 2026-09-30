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

## Deploy for free: Oracle Cloud + Vercel

The assistant cannot run in Render's 512 MB free web service. The fully free
deployment option is one Oracle Cloud Always Free ARM VM, which has enough
memory for the catalogue and retrieval services together. Create an Ubuntu
`VM.Standard.A1.Flex` instance in your Oracle home region with the available
Always Free CPU and memory allocation, allow inbound TCP port 80 in its OCI
security list, then SSH to it and run:

```bash
curl -fsSL https://raw.githubusercontent.com/GumalAditya06/SpecEngin-bis/main/deploy/oracle-free/bootstrap.sh | sudo bash
sudo nano /etc/specengin/assistant.env # Replace PASTE_A_NEW_KEY_HERE.
sudo systemctl restart specengin-assistant
```

The script installs both APIs without Docker, seeds the catalogue SQLite
database, and uses Nginx to expose them behind one VM IP address. The first
visit to `/health/ready` downloads and warms the retrieval models; it can take
several minutes.

Set both Vercel **Production** API variables to that base address and redeploy:

```text
NEXT_PUBLIC_DEMO_MODE=false
NEXT_PUBLIC_API_URL=http://YOUR_VM_PUBLIC_IP
NEXT_PUBLIC_RETRIEVAL_API_URL=http://YOUR_VM_PUBLIC_IP
```

The frontend proxies both APIs server-side, so browser CORS is not required.
For a proper HTTPS production endpoint, attach your own domain to the VM and
place Caddy or Nginx with a Let's Encrypt certificate in front of the services.

`render.yaml` remains available if you want the catalogue-only demo service on
Render's free tier. Its free Postgres database expires after 30 days, so the
Oracle VM route is preferred for a lasting zero-cost demo.

## Security

If a provider key has ever been pasted into a terminal recording, chat, issue,
or commit, revoke it in the provider console and create a new one. Configure
the replacement only in `scraper/.env` or deployment secrets.
