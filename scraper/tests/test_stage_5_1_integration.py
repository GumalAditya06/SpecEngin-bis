"""Stage 5.1 integration coverage across the production RAG boundaries.

These tests deliberately substitute only the external model call and retrieval
I/O where a failure must be induced.  The production evidence builder,
sufficiency guard, answer validator, FastAPI route, and response models remain
in the path.
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from scraper.assistant_api import app, get_llm_provider_factory
from scraper.evidence_context import EvidenceContextBuilder
from scraper.grounded_answer import GroundedAnswerService
from scraper.knowledge_entities import KnowledgeEntityService, standard_id
from scraper.llm_provider import (
    ProviderPermanentError,
    ProviderResponse,
    ProviderTransientError,
)
from scraper.retrieval import RetrievalError
from scraper.retrieval_api import get_retrieval_service
from scraper.retrieval_service import (
    EvidenceResult,
    EvidenceRetrieval,
    EvidenceSource,
    RetrievalResponse,
)


def evidence(
    rank: int = 1,
    *,
    chunk_id: str | None = None,
    standard: str | None = "IS 367:1993",
    clause: str | None = "4.2",
    text: str = "The appliance material and construction shall meet the requirement.",
    title: str | None = "Electric kettles and jugs",
    document_id: str | None = "ba52fdad4caf5bc088bd",
    url: str | None = "https://www.bis.gov.in/example.pdf",
    score: float = 4.2,
    methods: tuple[str, ...] = ("semantic", "bm25"),
) -> EvidenceResult:
    return EvidenceResult(
        rank=rank,
        chunk_id=chunk_id or f"chunk-{rank}",
        standard_number=standard,
        clause_number=clause,
        clause_title="Construction" if clause else None,
        text=text,
        context_prefix=" | ".join(value for value in (standard, f"Clause {clause}" if clause else None) if value),
        source=EvidenceSource(document_id, None, title, None, 7 + rank, url),
        retrieval=EvidenceRetrieval(methods, rank if "semantic" in methods else None, rank if "bm25" in methods else None, 0.03, score),
    )


class RoutedRetrieval:
    """Deterministic retrieval boundary with query-specific immutable results."""

    def retrieve(self, query, top_k=5, filters=None):
        clean = query.strip()
        if "99999" in clean or clean == "no evidence":
            results = ()
        elif "weather" in clean.casefold():
            results = (
                evidence(
                    text="The appliance shall carry the Standard Mark.",
                    score=-10.8,
                    methods=("semantic",),
                ),
            )
        elif "material and construction" in clean.casefold():
            results = (
                evidence(text="The material shall meet the specified requirement."),
                evidence(2, text="The construction shall meet the specified requirement."),
            )
        elif "4250" in clean:
            results = (evidence(standard="IS 4250:2025", document_id="d61562a653c02bee77be"),)
        elif "1.3.1" in clean:
            results = (evidence(standard="IS 2347:2023", clause="1.3.1", text="Control unit definition and requirement."),)
        elif "missing metadata" in clean:
            results = (evidence(title=None, document_id=None, url=None),)
        else:
            results = (evidence(),)
        return RetrievalResponse(query=clean, results=results[:top_k], timings={"total_ms": 8.5})


class QueryAwareProvider:
    provider_name = "fake"
    model = "stage-5.1-fake"

    def __init__(self):
        self.calls: list[dict[str, str]] = []
        self._lock = threading.Lock()

    def generate(self, system_instruction, user_query, evidence_context):
        with self._lock:
            self.calls.append({"query": user_query, "context": evidence_context})
        ids = [value for value in ("E1", "E2") if f"[{value}]" in evidence_context]
        cited = ids if "material and construction" in user_query.casefold() else ids[:1]
        partial = "installation threshold" in user_query.casefold()
        payload = {
            "answer": f"Answer for {user_query}. " + "".join(f"[{value}]" for value in cited),
            "evidence_state": "PARTIAL_EVIDENCE" if partial else "VERIFIED_EVIDENCE",
            "citation_ids": cited,
            "limitations": ["The installation threshold is not established."] if partial else [],
        }
        return ProviderResponse(text=json.dumps(payload), model=self.model, provider=self.provider_name)


@pytest.fixture
def api_dependencies():
    previous = dict(app.dependency_overrides)
    provider = QueryAwareProvider()
    app.dependency_overrides[get_retrieval_service] = RoutedRetrieval
    app.dependency_overrides[get_llm_provider_factory] = lambda: (lambda: provider)
    yield provider
    app.dependency_overrides.clear()
    app.dependency_overrides.update(previous)


@pytest.mark.parametrize(
    "query,expected_standard",
    [
        ("What appliance requirement applies?", "IS 367:1993"),
        ("IS 367", "IS 367:1993"),
        ("IS 4250:2025", "IS 4250:2025"),
        ("Clause 1.3.1 control unit", "IS 2347:2023"),
        ("What requirements apply to an electric kettle?", "IS 367:1993"),
        ("What testing requirement applies to the appliance?", "IS 367:1993"),
    ],
)
def test_end_to_end_request_shapes_use_one_production_contract(api_dependencies, query, expected_standard):
    response = TestClient(app).post("/api/v1/assistant/query", json={"query": query})
    assert response.status_code == 200
    payload = response.json()
    assert payload["evidence_state"] == "VERIFIED_EVIDENCE"
    assert payload["citations"][0]["standard_number"] == expected_standard
    assert payload["citations"][0]["evidence_id"] == payload["evidence"][0]["evidence_id"]
    assert payload["metadata"]["assessment_state"] == "SUFFICIENT"


def test_multi_evidence_and_partial_evidence_are_preserved(api_dependencies):
    client = TestClient(app)
    multi = client.post("/api/v1/assistant/query", json={"query": "material and construction"}).json()
    partial = client.post("/api/v1/assistant/query", json={"query": "IS 367 installation threshold"}).json()
    assert [item["evidence_id"] for item in multi["citations"]] == ["E1", "E2"]
    assert multi["metadata"]["supporting_evidence_count"] == 2
    assert partial["assessment"]["state"] == "PARTIAL"
    assert partial["evidence_state"] == "PARTIAL_EVIDENCE"
    assert partial["limitations"] == ["The installation threshold is not established."]


@pytest.mark.parametrize(
    "query,assessment",
    [("IS 99999:2099", "INSUFFICIENT"), ("What is the weather in Pune?", "OUT_OF_DOMAIN")],
)
def test_blocked_evidence_states_never_resolve_or_call_provider(api_dependencies, query, assessment):
    provider = api_dependencies
    before = len(provider.calls)
    payload = TestClient(app).post("/api/v1/assistant/query", json={"query": query}).json()
    assert payload["assessment"]["state"] == assessment
    assert payload["evidence_state"] == "NO_VERIFIED_EVIDENCE"
    assert payload["citations"] == [] and payload["evidence"] == []
    assert payload["metadata"]["gemini_call_avoided"] is True
    assert len(provider.calls) == before


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"query": ""},
        {"query": "x" * 2001},
        {"query": "valid", "filters": {"source_id": "user-controlled"}},
        {"query": "valid", "top_k": 6},
    ],
)
def test_request_contract_rejects_malformed_or_out_of_bounds_payloads(api_dependencies, body):
    assert TestClient(app).post("/api/v1/assistant/query", json=body).status_code == 422


def test_request_contract_accepts_the_documented_maximum_query(api_dependencies):
    response = TestClient(app).post("/api/v1/assistant/query", json={"query": "x" * 2000})
    assert response.status_code == 200


@pytest.mark.parametrize(
    "failure",
    [ProviderPermanentError("authentication rejected: secret detail"), ProviderTransientError("timeout")],
)
def test_provider_failures_are_controlled_and_do_not_disclose_upstream_details(api_dependencies, failure):
    class FailingProvider:
        provider_name = "fake"
        model = "fake"

        def generate(self, *_args):
            raise failure

    app.dependency_overrides[get_llm_provider_factory] = lambda: (lambda: FailingProvider())
    response = TestClient(app).post("/api/v1/assistant/query", json={"query": "appliance requirement"})
    assert response.status_code == 502
    assert "secret detail" not in response.text and "timeout" not in response.text


@pytest.mark.parametrize(
    "provider_text",
    [
        "not json",
        json.dumps({"answer": "Fabricated [E99].", "evidence_state": "VERIFIED_EVIDENCE", "citation_ids": ["E99"], "limitations": []}),
    ],
)
def test_malformed_output_and_unsupported_citations_fail_closed(api_dependencies, provider_text):
    class InvalidProvider:
        provider_name = "fake"
        model = "fake"

        def generate(self, *_args):
            return ProviderResponse(text=provider_text, model=self.model, provider=self.provider_name)

    app.dependency_overrides[get_llm_provider_factory] = lambda: (lambda: InvalidProvider())
    response = TestClient(app).post("/api/v1/assistant/query", json={"query": "appliance requirement"})
    assert response.status_code == 502
    assert response.json()["detail"] == "The configured model provider did not return a valid grounded answer."


def test_retrieval_failures_are_safe_and_unexpected_store_errors_are_generic(api_dependencies):
    class ExpectedFailure:
        def retrieve(self, *_args, **_kwargs):
            raise RetrievalError("retrieval unavailable")

    app.dependency_overrides[get_retrieval_service] = ExpectedFailure
    expected = TestClient(app).post("/api/v1/assistant/query", json={"query": "requirement"})
    assert expected.status_code == 422

    class StoreFailure:
        def retrieve(self, *_args, **_kwargs):
            raise RuntimeError("/private/vector/path and internal state")

    app.dependency_overrides[get_retrieval_service] = StoreFailure
    unexpected = TestClient(app, raise_server_exceptions=False).post(
        "/api/v1/assistant/query", json={"query": "requirement"}
    )
    assert unexpected.status_code == 500
    assert unexpected.text == "Internal Server Error"


def test_missing_optional_source_metadata_remains_missing(api_dependencies):
    payload = TestClient(app).post("/api/v1/assistant/query", json={"query": "missing metadata requirement"}).json()
    citation = payload["citations"][0]
    assert citation["document_id"] is None
    assert citation["title"] is None
    assert citation["url"] is None


def test_citation_chain_reaches_existing_chunk_unit_document_standard_and_page():
    entities = KnowledgeEntityService.from_data_dir("data")
    unit = next(
        item
        for item in entities.get_structural_units("ba52fdad4caf5bc088bd")
        if item.chunk_ids and item.pages
    )
    document = entities.get_source_document(unit.document_id)
    assert document is not None
    number = unit.standard_numbers[0]
    standard = entities.get_standard(number)
    assert standard is not None
    chunk_id = unit.chunk_ids[0]
    relation = entities.get_relationships("structural_unit", unit.structural_unit_id, "HAS_CHUNK")
    assert any(item.target_id == f"chunk:{chunk_id}" for item in relation)
    assert document.document_id in standard.document_ids
    assert unit.pages[0] > 0
    assert document.source_url and document.source_url.startswith("https://www.bis.gov.in/")


def test_metadata_conflict_and_review_state_propagate_across_relationship():
    entities = KnowledgeEntityService.from_data_dir("data")
    document = entities.get_source_document("2e278da9542436da23e7")
    assert document is not None and document.metadata_status == "REQUIRES_REVIEW"
    relation = next(
        item
        for item in entities.get_relationships("standard", standard_id("IS 4250:2025"), "HAS_DOCUMENT")
        if item.target_id == document.document_id
    )
    assert relation.metadata_status == "REQUIRES_REVIEW"
    assert set(document.conflict_types) <= set(relation.review_reasons)


def test_simultaneous_queries_do_not_mix_answers_evidence_or_citations():
    provider = QueryAwareProvider()
    service = GroundedAnswerService(RoutedRetrieval(), EvidenceContextBuilder(), provider)
    queries = [
        "IS 367",
        "IS 4250:2025",
        "Clause 1.3.1 control unit",
        "material and construction",
    ]
    with ThreadPoolExecutor(max_workers=len(queries)) as pool:
        answers = list(pool.map(service.answer, queries))
    by_query = {answer.query: answer for answer in answers}
    assert set(by_query) == set(queries)
    for query, answer in by_query.items():
        assert f"Answer for {query}." in answer.answer
        available = {item.evidence_id for item in answer.evidence}
        assert {item.evidence_id for item in answer.citations} <= available
        assert all(item.document_id == evidence.document_id for item in answer.citations for evidence in answer.evidence if item.evidence_id == evidence.evidence_id)
    assert by_query["IS 367"].citations[0].standard_number == "IS 367:1993"
    assert by_query["IS 4250:2025"].citations[0].standard_number == "IS 4250:2025"
    assert by_query["Clause 1.3.1 control unit"].citations[0].clause_number == "1.3.1"


def test_no_provider_resolution_for_blocked_query_even_when_factory_would_fail():
    def forbidden_provider():
        raise AssertionError("provider factory must remain lazy")

    answer = GroundedAnswerService(
        RoutedRetrieval(), EvidenceContextBuilder(), forbidden_provider
    ).answer("What is the weather in Pune?")
    assert answer.evidence_state == "NO_VERIFIED_EVIDENCE"
    assert answer.metadata.gemini_call_avoided is True
