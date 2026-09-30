"""Production boundary helpers that do not alter the frozen RAG pipeline."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MAX_REQUEST_BYTES = 16 * 1024
REQUIRED_RETRIEVAL_ASSETS = (
    "processed/chunks_v2/chunks.jsonl",
    "processed/embeddings_v2/embeddings.jsonl",
    "processed/vector_store/index.npz",
    "processed/vector_store/index_config.json",
    "processed/vector_store/metadata.jsonl",
)

logger = logging.getLogger("specengine-bis.production")
_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "specengine_request_id", default=None
)


def environment_name() -> str:
    value = os.environ.get("SPECENGINE_ENV", "development").strip().lower()
    if value not in {"development", "demo", "production", "test"}:
        raise RuntimeError("SPECENGINE_ENV must be development, demo, production, or test")
    return value


def cors_origins() -> list[str]:
    raw = os.environ.get("SPECENGINE_CORS_ORIGINS")
    if raw is None:
        return [] if environment_name() == "production" else ["http://localhost:3000"]
    values = [value.strip().rstrip("/") for value in raw.split(",") if value.strip()]
    if "*" in values:
        raise RuntimeError("wildcard CORS origins are not permitted")
    if environment_name() == "production" and any(
        not value.startswith("https://") for value in values
    ):
        raise RuntimeError("production CORS origins must use HTTPS")
    return values


def max_request_bytes() -> int:
    raw = os.environ.get("SPECENGINE_MAX_REQUEST_BYTES", str(DEFAULT_MAX_REQUEST_BYTES))
    try:
        value = int(raw)
    except ValueError as exc:
        raise RuntimeError("SPECENGINE_MAX_REQUEST_BYTES must be an integer") from exc
    if value < 4096:
        raise RuntimeError("SPECENGINE_MAX_REQUEST_BYTES must be at least 4096")
    return value


def resolve_data_dir() -> Path:
    root_raw = os.environ.get("SPECENGINE_DATA_ROOT")
    root = Path(root_raw).expanduser() if root_raw else PROJECT_ROOT / "data"
    if not root.is_absolute():
        root = PROJECT_ROOT / root
    root = root.resolve()
    configured = os.environ.get("SPECENGINE_DATA_DIR")
    if configured:
        candidate = Path(configured).expanduser()
        if not candidate.is_absolute():
            candidate = PROJECT_ROOT / candidate
    else:
        candidate = root
    candidate = candidate.resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise RuntimeError("SPECENGINE_DATA_DIR must be within SPECENGINE_DATA_ROOT") from exc
    return candidate


def missing_retrieval_assets(data_dir: Path) -> list[str]:
    return [relative for relative in REQUIRED_RETRIEVAL_ASSETS if not (data_dir / relative).is_file()]


def provider_is_configured() -> bool:
    return all(
        os.environ.get(name, "").strip()
        for name in ("BIS_LLM_PROVIDER", "BIS_LLM_MODEL", "BIS_LLM_API_KEY")
    )


def current_request_id() -> str | None:
    return _request_id.get()


def log_event(level: int, event: str, **fields: Any) -> None:
    safe = {"event": event, "request_id": current_request_id(), **fields}
    logger.log(level, json.dumps(safe, sort_keys=True, separators=(",", ":")))


class ProductionBoundaryMiddleware:
    """Bound request bodies and emit privacy-conscious request telemetry."""

    def __init__(self, app: Callable[..., Awaitable[None]]):
        self.app = app

    async def __call__(self, scope: dict[str, Any], receive: Callable, send: Callable) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        request_id = uuid.uuid4().hex
        token = _request_id.set(request_id)
        status_code = 500
        try:
            body_messages: list[dict[str, Any]] = []
            total = 0
            limit = max_request_bytes()
            while True:
                message = await receive()
                body_messages.append(message)
                if message["type"] == "http.disconnect":
                    return
                total += len(message.get("body", b""))
                if total > limit:
                    status_code = 413
                    payload = b'{"detail":"Request body is too large."}'
                    await send(
                        {
                            "type": "http.response.start",
                            "status": status_code,
                            "headers": [
                                (b"content-type", b"application/json"),
                                (b"content-length", str(len(payload)).encode("ascii")),
                                (b"x-request-id", request_id.encode("ascii")),
                            ],
                        }
                    )
                    await send({"type": "http.response.body", "body": payload})
                    return
                if not message.get("more_body", False):
                    break

            message_index = 0

            async def replay_receive() -> dict[str, Any]:
                nonlocal message_index
                if message_index < len(body_messages):
                    message = body_messages[message_index]
                    message_index += 1
                    return message
                return {"type": "http.request", "body": b"", "more_body": False}

            async def guarded_send(message: dict[str, Any]) -> None:
                nonlocal status_code
                if message["type"] == "http.response.start":
                    status_code = int(message["status"])
                    headers = list(message.get("headers", []))
                    headers.append((b"x-request-id", request_id.encode("ascii")))
                    message = {**message, "headers": headers}
                await send(message)

            await self.app(scope, replay_receive, guarded_send)
        finally:
            duration_ms = round((time.perf_counter() - started) * 1000, 3)
            log_event(
                logging.INFO if status_code < 500 else logging.ERROR,
                "http_request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status_code,
                duration_ms=duration_ms,
            )
            _request_id.reset(token)
