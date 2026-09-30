"""HTTP contract tests for the Stage 4.2 grounded assistant endpoint."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient

from scraper.assistant_api import (
    app,
    get_context_builder,
    get_llm_provider,
    get_llm_provider_factory,
)
from scraper.llm_provider import (
    FakeLLMProvider,
    ProviderPermanentError,
    ProviderResponse,
)
from scraper.retrieval_api import get_retrieval_service
from scraper.retrieval_service import (
    EvidenceResult,
    EvidenceRetrieval,
    EvidenceSource,
    RetrievalResponse,
)


def evidence() -> EvidenceResult:
    return EvidenceResult(
        rank=1,
        chunk_id="chunk-a",
        standard_number="IS 367:1993",
        clause_number="4.2",
        clause_title="Construction",
        text="The appliance shall meet the specified requirement.",
        context_prefix="IS 367:1993 | Clause 4.2 — Construction",
        source=EvidenceSource(
            "doc-a",
            None,
            "Electric kettles and jugs",
            None,
            12,
            "https://example.invalid/is-367.pdf",
        ),
        retrieval=EvidenceRetrieval(("semantic", "bm25"), 2, 1, 0.031, 4.2),
    )


class FakeRetrieval:
    def __init__(self):
        self.calls = []

    def retrieve(self, query, top_k=5, filters=None):
        self.calls.append((query, top_k, filters))
        if query == "no evidence":
            results = ()
        elif "weather" in query.casefold():
            weak = replace(
                evidence(),
                retrieval=EvidenceRetrieval(("semantic",), 1, None, 0.015, -10.8),
                text="The appliance shall carry the Standard Mark.",
                context_prefix="IS 367:1993 | Marking",
            )
            results = (weak,)
        else:
            results = (evidence(),)
        return RetrievalResponse(query=query, results=results, timings={"total_ms": 5.0})


def draft(state="VERIFIED_EVIDENCE") -> ProviderResponse:
    partial = state == "PARTIAL_EVIDENCE"
    return ProviderResponse(
        text=json.dumps(
            {
                "answer": (
                    "The supplied evidence does not establish the requested weather. [E1]"
                    if partial
                    else "The requirement is established. [E1]"
                ),
                "evidence_state": state,
                "citation_ids": ["E1"],
                "limitations": (
                    ["The requested fact is not established by BIS evidence."]
                    if partial
                    else []
                ),
            }
        ),
        model="fake-model",
        provider="fake",
    )


@pytest.fixture
def api_state():
    previous = dict(app.dependency_overrides)
    app.dependency_overrides.clear()
    retrieval = FakeRetrieval()
    provider = FakeLLMProvider([draft(), draft("PARTIAL_EVIDENCE")])
    app.dependency_overrides[get_retrieval_service] = lambda: retrieval
    app.dependency_overrides[get_llm_provider_factory] = lambda: (lambda: provider)
    app.openapi_schema = None
    yield retrieval, provider
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)
    app.openapi_schema = None


def test_assistant_api_response_schema_and_provenance(api_state):
    response = TestClient(app).post(
        "/api/v1/assistant/query",
        json={"query": "IS 367", "top_k": 5, "filters": {}},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["query"] == "IS 367"
    assert payload["evidence_state"] == "VERIFIED_EVIDENCE"
    assert payload["citations"] == [
        {
            "evidence_id": "E1",
            "standard_number": "IS 367:1993",
            "clause_number": "4.2",
            "clause_title": "Construction",
            "document_id": "doc-a",
            "source_id": None,
            "title": "Electric kettles and jugs",
            "version": None,
            "page": 12,
            "url": "https://example.invalid/is-367.pdf",
        }
    ]
    assert payload["evidence"][0]["authoritative_text"].startswith("The appliance")
    assert payload["metadata"]["provider"] == "fake"

    schema = TestClient(app).get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/assistant/query"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/GroundedAnswer")


def test_exact_identifier_and_filters_are_passed_to_retrieval_unchanged(api_state):
    retrieval, _ = api_state
    response = TestClient(app).post(
        "/api/v1/assistant/query",
        json={
            "query": "IS 367",
            "top_k": 3,
            "filters": {"standard_number": "IS 367:1993"},
        },
    )
    assert response.status_code == 200
    assert retrieval.calls == [
        ("IS 367", 3, {"standard_number": "IS 367:1993"})
    ]


def test_unsupported_question_is_guarded_without_provider_call(api_state):
    _, provider = api_state
    response = TestClient(app).post(
        "/api/v1/assistant/query",
        json={"query": "What is the weather in Pune?"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence_state"] == "NO_VERIFIED_EVIDENCE"
    assert payload["assessment"]["state"] == "OUT_OF_DOMAIN"
    assert payload["citations"] == [] and payload["evidence"] == []
    assert payload["limitations"]
    assert provider.calls == []


def test_empty_evidence_does_not_call_provider(api_state):
    _, provider = api_state
    response = TestClient(app).post(
        "/api/v1/assistant/query",
        json={"query": "no evidence"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence_state"] == "NO_VERIFIED_EVIDENCE"
    assert payload["citations"] == []
    assert provider.calls == []


@pytest.mark.parametrize(
    "body",
    [
        {"query": ""},
        {"query": "requirements", "top_k": 0},
        {"query": "requirements", "top_k": 6},
        {"query": "requirements", "filters": {"source_id": "unsupported"}},
        {"query": "requirements", "unknown": True},
    ],
)
def test_api_request_validation(api_state, body):
    assert TestClient(app).post("/api/v1/assistant/query", json=body).status_code == 422


def test_provider_failure_returns_controlled_502(api_state):
    _, provider = api_state
    provider._responses.clear()
    provider._responses.append(ProviderPermanentError("secret upstream detail"))
    response = TestClient(app).post(
        "/api/v1/assistant/query", json={"query": "requirements"}
    )
    assert response.status_code == 502
    assert response.json()["detail"] == (
        "The configured model provider did not return a valid grounded answer."
    )
    assert "secret" not in response.text


def test_provider_failure_logs_only_structured_safe_diagnostics(api_state, caplog):
    _, provider = api_state
    provider._responses.clear()
    provider._responses.append(
        ProviderPermanentError(
            "secret upstream detail",
            upstream_status=403,
            provider_error_category="PERMISSION_DENIED",
            provider="google_gemini",
        )
    )
    with caplog.at_level("ERROR", logger="specengine-bis.production"):
        response = TestClient(app).post(
            "/api/v1/assistant/query", json={"query": "requirements"}
        )
    assert response.status_code == 502
    log_text = "\n".join(record.message for record in caplog.records)
    assert '"upstream_status":403' in log_text
    assert '"provider_error_category":"PERMISSION_DENIED"' in log_text
    assert '"provider_error_field":null' in log_text
    assert "secret upstream detail" not in log_text


def test_missing_provider_configuration_returns_503(api_state, monkeypatch):
    app.dependency_overrides.pop(get_llm_provider_factory)
    get_llm_provider.cache_clear()
    for name in ("BIS_LLM_PROVIDER", "BIS_LLM_API_KEY", "BIS_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    response = TestClient(app).post(
        "/api/v1/assistant/query", json={"query": "requirements"}
    )
    assert response.status_code == 503
    assert response.json()["detail"] == "The answer provider is not configured."
    assert "BIS_LLM" not in response.text
