"""Stage 3.4 service boundary and evidence-contract tests."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scraper.hybrid_retrieval import HybridResult
from scraper.retrieval import RetrievalError
from scraper.retrieval_service import RetrievalService, evidence_from_hybrid


def record(chunk_id: str, standard: str, **overrides) -> dict:
    value = {
        "chunk_id": chunk_id,
        "standard_numbers": [standard],
        "document_id": f"doc-{chunk_id}",
        "document_title": f"Document {chunk_id}",
        "document_type": "product_manual",
        "clause_number": "4",
        "clause_title": "Requirements",
        "context_prefix": f"{standard} | Clause 4 — Requirements",
        "pages": [2],
        "source_url": f"https://example.invalid/{chunk_id}.pdf",
        "text": f"Authoritative evidence {chunk_id}",
    }
    value.update(overrides)
    return value


class StubHybrid:
    def __init__(self, records):
        self.records = records
        self.bm25 = SimpleNamespace(records=records)
        self.calls = []

    def search(self, query, **kwargs):
        self.calls.append((query, kwargs))
        filters = kwargs.get("filters") or {}
        selected = [item for item in self.records if all(
            value in item.get("standard_numbers", []) if field == "standard_number"
            else str(item.get(field)) == str(value)
            for field, value in filters.items()
        )]
        results = [
            HybridResult(
                rank=rank,
                chunk_id=item["chunk_id"],
                reranker_score=5.0 - rank,
                fusion_score=2 / (60 + rank),
                semantic_rank=rank,
                bm25_rank=rank,
                record=item,
            )
            for rank, item in enumerate(selected[:kwargs["final_k"]], start=1)
        ]
        return results, {"total_ms": 1.25, "reranking_ms": 1.0}


@pytest.fixture
def records():
    return [
        record("a", "IS 4250:2025", source_id="source-a", document_version="2025"),
        record("b", "IS 367:1993", clause_number="1.3.1"),
        record("c", "IS 2347:2023"),
    ]


@pytest.fixture
def service(records):
    return RetrievalService(StubHybrid(records))


def test_evidence_contract_preserves_provenance(records):
    evidence = evidence_from_hybrid(HybridResult(1, "a", 4.2, 0.031, 2, 1, records[0]))
    payload = evidence.to_dict()
    assert payload["chunk_id"] == "a"
    assert payload["standard_number"] == "IS 4250:2025"
    assert payload["text"] == "Authoritative evidence a"
    assert payload["source"] == {
        "document_id": "doc-a", "source_id": "source-a", "title": "Document a",
        "version": "2025", "page": 2, "url": "https://example.invalid/a.pdf",
    }
    assert payload["retrieval"]["methods"] == ("semantic", "bm25")


@pytest.mark.parametrize("query,expected", [
    ("IS 367", "IS 367:1993"),
    ("IS 2347", "IS 2347:2023"),
    ("IS 4250", "IS 4250:2025"),
])
def test_short_exact_identifier_restricts_final_results(service, query, expected):
    response = service.retrieve(query)
    assert response.results
    assert {item.standard_number for item in response.results} == {expected}
    assert service._retriever.calls[-1][1]["filters"]["standard_number"] == expected


def test_full_exact_identifier_is_restricted(service):
    response = service.retrieve("requirements in IS 367:1993")
    assert [item.chunk_id for item in response.results] == ["b"]


def test_conflicting_identifier_and_filter_returns_empty(service):
    response = service.retrieve("IS 367", filters={"standard_number": "IS 4250:2025"})
    assert response.results == ()
    assert service._retriever.calls == []


def test_unknown_exact_identifier_returns_empty_without_reranking(service):
    response = service.retrieve("IS 99999")
    assert response.results == () and response.timings == {}
    assert service._retriever.calls == []


def test_service_uses_frozen_stage_33_configuration(service):
    service.retrieve("control unit", top_k=3)
    kwargs = service._retriever.calls[-1][1]
    assert kwargs["candidate_k"] == 20
    assert kwargs["rerank_k"] == 40
    assert kwargs["rrf_k"] == 60
    assert kwargs["final_k"] == 3


def test_supported_filter_is_applied_before_hybrid_search(service):
    response = service.retrieve("requirements", filters={"document_id": "doc-b"})
    assert [item.chunk_id for item in response.results] == ["b"]
    assert service._retriever.calls[-1][1]["filters"] == {"document_id": "doc-b"}


@pytest.mark.parametrize("filters", [
    {"source_id": "not-supported"}, {"document_id": []}, {"clause_number": " "},
])
def test_malformed_or_unsupported_filters_are_rejected(service, filters):
    with pytest.raises(RetrievalError):
        service.retrieve("requirements", filters=filters)


@pytest.mark.parametrize("query", ["", "   ", None])
def test_empty_query_is_rejected(service, query):
    with pytest.raises(RetrievalError, match="non-empty"):
        service.retrieve(query)  # type: ignore[arg-type]


@pytest.mark.parametrize("top_k", [0, 6, True])
def test_top_k_preserves_final_top_five_policy(service, top_k):
    with pytest.raises(RetrievalError, match="between 1 and 5"):
        service.retrieve("requirements", top_k=top_k)


def test_repeated_queries_have_deterministic_order(service):
    first = service.retrieve("requirements").to_dict()
    second = service.retrieve("requirements").to_dict()
    assert first == second


def test_unavailable_provenance_is_null_not_invented(records):
    evidence = evidence_from_hybrid(HybridResult(1, "b", 1.0, 0.01, 1, None, records[1]))
    assert evidence.source.source_id is None
    assert evidence.source.version is None
    assert evidence.retrieval.methods == ("semantic",)
