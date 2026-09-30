"""HTTP contract tests for POST /api/v1/search/retrieve."""

from __future__ import annotations

from fastapi.testclient import TestClient

from scraper.retrieval_api import app, get_retrieval_service
from scraper.retrieval_service import (
    EvidenceResult,
    EvidenceRetrieval,
    EvidenceSource,
    RetrievalResponse,
)


class FakeService:
    def retrieve(self, query, top_k=5, filters=None):
        if query == "nothing":
            return RetrievalResponse(query=query, results=(), timings={})
        evidence = EvidenceResult(
            rank=1,
            chunk_id="chunk-a",
            standard_number="IS 367:1993",
            clause_number="1.3.1",
            clause_title="Scope",
            text="Verbatim BIS evidence.",
            context_prefix="IS 367:1993 | Clause 1.3.1 — Scope",
            source=EvidenceSource("doc-a", None, "Electric kettles", None, 4, "https://example.invalid/a.pdf"),
            retrieval=EvidenceRetrieval(("semantic", "bm25"), 2, 1, 0.031, 4.2),
        )
        return RetrievalResponse(query=query, results=(evidence,)[:top_k], timings={"total_ms": 5})


app.dependency_overrides[get_retrieval_service] = lambda: FakeService()
client = TestClient(app)


def test_api_response_schema_and_frontend_compatibility():
    response = client.post("/api/v1/search/retrieve", json={"query": "IS 367", "top_k": 5, "filters": {}})
    assert response.status_code == 200
    assert response.json() == {
        "query": "IS 367",
        "results": [{
            "rank": 1, "chunk_id": "chunk-a", "standard_number": "IS 367:1993",
            "clause_number": "1.3.1", "clause_title": "Scope",
            "text": "Verbatim BIS evidence.",
            "context_prefix": "IS 367:1993 | Clause 1.3.1 — Scope",
            "source": {"document_id": "doc-a", "source_id": None, "title": "Electric kettles", "version": None, "page": 4, "url": "https://example.invalid/a.pdf"},
            "retrieval": {"methods": ["semantic", "bm25"], "semantic_rank": 2, "bm25_rank": 1, "rrf_score": 0.031, "reranker_score": 4.2},
        }],
    }

    schema = client.get("/openapi.json").json()
    operation = schema["paths"]["/api/v1/search/retrieve"]["post"]
    assert operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"].endswith("/RetrieveResponse")


def test_api_empty_result_contract():
    response = client.post("/api/v1/search/retrieve", json={"query": "nothing"})
    assert response.status_code == 200
    assert response.json() == {"query": "nothing", "results": []}


def test_api_rejects_empty_query():
    assert client.post("/api/v1/search/retrieve", json={"query": ""}).status_code == 422


def test_api_rejects_malformed_filters():
    response = client.post("/api/v1/search/retrieve", json={"query": "scope", "filters": {"source_id": "x"}})
    assert response.status_code == 422


def test_api_rejects_top_k_above_policy():
    assert client.post("/api/v1/search/retrieve", json={"query": "scope", "top_k": 6}).status_code == 422
