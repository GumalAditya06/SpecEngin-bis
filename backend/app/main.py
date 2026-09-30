from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router as api_router
from app.core.config import settings

# Values that mark an unconfigured LLM key. Keep in sync with .env guidance.
_LLM_KEY_PLACEHOLDERS = {
    "",
    "replace_with_your_google_ai_studio_key",
}


def _check_llm_key(logger) -> None:
    """Warn when the LLM path cannot activate.

    An empty key means extractive-only mode; a placeholder value means the
    .env was copied but never filled in. Neither is fatal — the API serves
    grounded extractive answers either way.
    """
    key = settings.llm_api_key.strip().lower()
    if key in _LLM_KEY_PLACEHOLDERS:
        logger.warning(
            "BIS_LLM_API_KEY is %s — LLM generation is disabled and answers "
            "will be extractive only. Set a Google AI Studio key "
            "(https://aistudio.google.com/apikey) in backend/.env to enable it.",
            "empty" if not key else "still the placeholder",
        )
    else:
        logger.info(
            "LLM generation enabled (model=%s, api_url=%s)",
            settings.llm_model,
            settings.llm_api_url,
        )

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup: ensure DB tables exist. If the database is unreachable the
    # service still starts — /api/v1/health stays truthful about liveness and
    # /api/v1/health/db reports the actual failure (no fake success).
    import logging

    from app.core.db import engine
    from app.models.base import Base

    logger = logging.getLogger("specengine-bis")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception as exc:  # pragma: no cover - depends on live DB state
        logger.warning(
            "could not create tables at startup (%s); "
            "run with a reachable database before ingesting",
            type(exc).__name__,
        )
    _check_llm_key(logger)
    yield

app = FastAPI(
    title="Specengine-BIS API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router, prefix=settings.api_v1_prefix)
