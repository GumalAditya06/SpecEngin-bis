"""Focused production-boundary regression tests for Stage 5.2."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from scraper.assistant_api import app
from scraper.production import (
    REQUIRED_RETRIEVAL_ASSETS,
    cors_origins,
    resolve_data_dir,
)
from scraper import retrieval_api
from scraper.retrieval_api import get_retrieval_service
from scraper.retrieval_service import RetrievalResponse


class EmptyRetrieval:
    def retrieve(self, query, top_k=5, filters=None):
        return RetrievalResponse(query=query, results=(), timings={"total_ms": 0.1})


@pytest.fixture
def client():
    previous = dict(app.dependency_overrides)
    app.dependency_overrides[get_retrieval_service] = lambda: EmptyRetrieval()
    yield TestClient(app, raise_server_exceptions=False)
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


def test_malformed_json_and_unsupported_fields_fail_closed(client):
    malformed = client.post(
        "/api/v1/assistant/query",
        content=b'{"query":',
        headers={"content-type": "application/json"},
    )
    unsupported = client.post(
        "/api/v1/assistant/query", json={"query": "valid", "admin": True}
    )
    assert malformed.status_code == 422
    assert unsupported.status_code == 422


def test_query_length_contract_accepts_2000_and_rejects_2001(client):
    accepted = client.post("/api/v1/assistant/query", json={"query": "x" * 2000})
    rejected = client.post("/api/v1/assistant/query", json={"query": "x" * 2001})
    assert accepted.status_code == 200
    assert rejected.status_code == 422
    assert "x" * 2001 not in rejected.text


def test_oversized_body_is_rejected_before_validation(client, monkeypatch):
    monkeypatch.setenv("SPECENGINE_MAX_REQUEST_BYTES", "4096")
    response = client.post(
        "/api/v1/assistant/query",
        content=json.dumps({"query": "x", "padding": "s" * 5000}),
        headers={"content-type": "application/json"},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "Request body is too large."}
    assert response.headers["x-request-id"]


def test_http_methods_and_request_ids(client):
    wrong_method = client.put("/api/v1/assistant/query", json={"query": "x"})
    normal = client.post("/api/v1/assistant/query", json={"query": "x"})
    assert wrong_method.status_code == 405
    assert normal.headers["x-request-id"]
    assert wrong_method.headers["x-request-id"]


def test_production_cors_requires_explicit_https_and_rejects_wildcard(monkeypatch):
    monkeypatch.setenv("SPECENGINE_ENV", "production")
    monkeypatch.delenv("SPECENGINE_CORS_ORIGINS", raising=False)
    assert cors_origins() == []
    monkeypatch.setenv("SPECENGINE_CORS_ORIGINS", "https://app.example")
    assert cors_origins() == ["https://app.example"]
    monkeypatch.setenv("SPECENGINE_CORS_ORIGINS", "*")
    with pytest.raises(RuntimeError, match="wildcard"):
        cors_origins()
    monkeypatch.setenv("SPECENGINE_CORS_ORIGINS", "http://app.example")
    with pytest.raises(RuntimeError, match="HTTPS"):
        cors_origins()


def test_data_directory_cannot_escape_configured_root(tmp_path, monkeypatch):
    root = tmp_path / "data"
    root.mkdir()
    monkeypatch.setenv("SPECENGINE_DATA_ROOT", str(root))
    monkeypatch.setenv("SPECENGINE_DATA_DIR", str(tmp_path / "outside"))
    with pytest.raises(RuntimeError, match="within SPECENGINE_DATA_ROOT"):
        resolve_data_dir()


def test_relative_data_root_is_resolved_from_application_root(monkeypatch):
    monkeypatch.setenv("SPECENGINE_DATA_ROOT", "data")
    monkeypatch.setenv("SPECENGINE_DATA_DIR", "data")
    assert resolve_data_dir() == (Path(__file__).parents[1] / "data").resolve()


def test_liveness_does_not_require_models_or_provider(client):
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "alive", "service": "specengine-bis"}


def _create_assets(root: Path) -> None:
    for relative in REQUIRED_RETRIEVAL_ASSETS:
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")


def test_readiness_distinguishes_optional_provider(tmp_path, monkeypatch, client):
    _create_assets(tmp_path)
    monkeypatch.setenv("SPECENGINE_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SPECENGINE_DATA_DIR", str(tmp_path))
    for name in ("BIS_LLM_PROVIDER", "BIS_LLM_MODEL", "BIS_LLM_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(retrieval_api, "get_retrieval_service", lambda: EmptyRetrieval())
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["components"] == {
        "application": "available",
        "retrieval_assets": "available",
        "models": "available",
        "provider": "unconfigured",
    }


def test_readiness_failure_redacts_paths_and_exception_details(tmp_path, monkeypatch, client):
    _create_assets(tmp_path)
    monkeypatch.setenv("SPECENGINE_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SPECENGINE_DATA_DIR", str(tmp_path))

    def fail():
        raise RuntimeError("secret-key=/private/model/path")

    monkeypatch.setattr(retrieval_api, "get_retrieval_service", fail)
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["components"]["models"] == "unavailable"
    assert "secret-key" not in response.text
    assert "/private" not in response.text


def test_missing_assets_do_not_disclose_filesystem_path(tmp_path, monkeypatch, client):
    monkeypatch.setenv("SPECENGINE_DATA_ROOT", str(tmp_path))
    monkeypatch.setenv("SPECENGINE_DATA_DIR", str(tmp_path))
    response = client.get("/health/ready")
    assert response.status_code == 503
    assert response.json()["detail"]["components"]["retrieval_assets"] == "unavailable"
    assert str(tmp_path) not in response.text


def test_retrieval_error_response_does_not_expose_internal_detail(client):
    class FailingRetrieval:
        def retrieve(self, *_args, **_kwargs):
            from scraper.retrieval import RetrievalError

            raise RetrievalError("api_key=secret /private/vector/index")

    app.dependency_overrides[get_retrieval_service] = lambda: FailingRetrieval()
    response = client.post("/api/v1/assistant/query", json={"query": "requirements"})
    assert response.status_code == 422
    assert response.json()["detail"] == (
        "The request could not be evaluated against retrieval evidence."
    )
    assert "secret" not in response.text and "/private" not in response.text
