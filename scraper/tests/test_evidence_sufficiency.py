"""Stage 4.6 deterministic evidence-sufficiency guard tests."""

from __future__ import annotations

import json

from scraper.evidence_context import EvidenceContextBuilder
from scraper.evidence_sufficiency import (
    MIN_RERANKER_SUPPORT_SCORE,
    EvidenceSufficiencyGuard,
    answer_state_for_assessment,
    supporting_context,
)
from scraper.grounded_answer import AnswerValidator, GroundedAnswerService
from scraper.llm_provider import FakeLLMProvider, ProviderResponse
from scraper.retrieval_service import (
    EvidenceResult,
    EvidenceRetrieval,
    EvidenceSource,
    RetrievalResponse,
)


def result(
    *,
    rank=1,
    chunk_id="chunk-a",
    standard="IS 367:1993",
    clause="4.2",
    text="Electric kettles shall meet the testing requirement.",
    context="IS 367:1993 | Clause 4.2 — Testing requirement",
    methods=("semantic", "bm25"),
    semantic_rank=1,
    bm25_rank=1,
    score=4.2,
):
    return EvidenceResult(
        rank=rank,
        chunk_id=chunk_id,
        standard_number=standard,
        clause_number=clause,
        clause_title="Testing requirement",
        text=text,
        context_prefix=context,
        source=EvidenceSource(
            "doc-a", None, "Electric kettles and jugs", None, 12,
            "https://example.invalid/is-367.pdf",
        ),
        retrieval=EvidenceRetrieval(methods, semantic_rank, bm25_rank, 0.031, score),
    )


def package(query, results):
    return EvidenceContextBuilder().build(query, results)


def assessment(query, results):
    return EvidenceSufficiencyGuard().assess(package(query, results))


def provider_response(state="VERIFIED_EVIDENCE"):
    return ProviderResponse(
        text=json.dumps(
            {
                "answer": "The testing requirement is established. [E1]",
                "evidence_state": state,
                "citation_ids": ["E1"],
                "limitations": ["Licensing conditions are not established."] if state == "PARTIAL_EVIDENCE" else [],
            }
        ),
        model="fake-model",
        provider="fake",
    )


class Retrieval:
    def __init__(self, results):
        self.results = tuple(results)

    def retrieve(self, query, top_k=5, filters=None):
        return RetrievalResponse(query=query, results=self.results, timings={"total_ms": 1.0})


def test_strong_standard_query_is_sufficient():
    value = assessment("What requirements apply under IS 367:1993?", [result()])
    assert value.state == "SUFFICIENT"
    assert value.answer_evidence_state == "VERIFIED_EVIDENCE"
    assert value.supporting_evidence_ids == ["E1"]


def test_exact_standard_identifier_overrides_low_semantic_score():
    weak = result(methods=("semantic",), bm25_rank=None, score=-8.0)
    value = assessment("IS 367:1993", [weak])
    assert value.state == "SUFFICIENT"
    assert value.reason == "exact_identifier_match"


def test_exact_clause_identifier_is_sufficient_when_present():
    weak = result(methods=("semantic",), bm25_rank=None, score=-2.0)
    value = assessment("What does Clause 4.2 require?", [weak])
    assert value.state == "SUFFICIENT"
    assert value.signals.matched_clause_identifiers == ["4.2"]


def test_strong_configured_product_query_is_sufficient():
    semantic = result(methods=("semantic",), bm25_rank=None, score=0.5)
    value = assessment("What applies to an electric kettle?", [semantic])
    assert value.state == "SUFFICIENT"
    assert value.signals.matched_product_ids == ["kettle"]


def test_compound_partially_supported_question_is_partial():
    testing_only = result(text="Testing conditions are specified for the appliance.")
    value = assessment("What testing and licensing conditions apply?", [testing_only])
    assert value.state == "PARTIAL"
    assert value.answer_evidence_state == "PARTIAL_EVIDENCE"
    assert value.signals.supported_segment_count == 1


def test_unknown_standard_is_insufficient():
    value = assessment("What does IS 99999:2099 require?", [result()])
    assert value.state == "INSUFFICIENT"
    assert value.reason == "unresolved_exact_standard"


def test_weather_query_is_out_of_domain():
    weak = result(
        text="The appliance shall carry the Standard Mark.",
        context="IS 367:1993 | Marking",
        methods=("semantic",), bm25_rank=None, score=-10.8,
    )
    value = assessment("What is the weather in Pune?", [weak])
    assert value.state == "OUT_OF_DOMAIN"
    assert value.signals.lexical_support_count == 0
    assert value.signals.bm25_support_count == 0


def test_generic_unrelated_query_is_out_of_domain_without_topic_blacklist():
    weak = result(
        text="Sampling shall follow Table 1.", context="IS 367:1993 | Sampling",
        methods=("semantic",), bm25_rank=None, score=-1.0,
    )
    assert assessment("Explain orbital mechanics", [weak]).state == "OUT_OF_DOMAIN"


def test_empty_package_is_insufficient_and_maps_to_no_evidence():
    value = assessment("What is required?", [])
    assert value.state == "INSUFFICIENT"
    assert value.answer_evidence_state == "NO_VERIFIED_EVIDENCE"
    assert value.signals.retrieval_candidate_count == 0


def test_weak_nearest_neighbor_is_not_automatically_verified():
    weak = result(
        text="Sampling shall follow Table 1.", context="IS 367:1993 | Sampling",
        methods=("semantic",), bm25_rank=None, score=0.2,
    )
    assert weak.retrieval.reranker_score >= MIN_RERANKER_SUPPORT_SCORE
    assert assessment("Explain orbital mechanics", [weak]).state == "OUT_OF_DOMAIN"


def test_evidence_ids_and_package_are_not_mutated():
    source = package("IS 367", [result()])
    before = source.model_dump(mode="json")
    value = EvidenceSufficiencyGuard().assess(source)
    assert value.supporting_evidence_ids == ["E1"]
    assert source.model_dump(mode="json") == before


def test_supporting_context_preserves_exact_authoritative_text():
    first = result()
    second = result(
        rank=2, chunk_id="chunk-b", standard="IS 4250:2025", clause="2",
        text="Unrelated lower-ranked material.", score=-2.0,
    )
    source = package("IS 367", [first, second])
    context = supporting_context(source, ["E1"])
    assert first.text in context
    assert second.text not in context
    assert "[E1]" in context and "[E2]" not in context


def test_existing_citation_reconstruction_is_unchanged():
    source = package("IS 367", [result()])
    _, citations = AnswerValidator().validate(provider_response(), source)
    assert citations[0].evidence_id == "E1"
    assert citations[0].standard_number == "IS 367:1993"
    assert citations[0].document_id == "doc-a"


def test_verified_query_shapes_remain_sufficient():
    queries = [
        "IS 367",
        "What is the testing requirement for electric kettles?",
        "What does Clause 4.2 require?",
    ]
    assert [assessment(query, [result()]).state for query in queries] == [
        "SUFFICIENT", "SUFFICIENT", "SUFFICIENT"
    ]


def test_out_of_domain_skips_provider_generation():
    weak = result(
        text="The appliance shall carry the Standard Mark.",
        context="IS 367:1993 | Marking",
        methods=("semantic",), bm25_rank=None, score=-10.8,
    )
    provider = FakeLLMProvider([])
    service = GroundedAnswerService(
        Retrieval([weak]), EvidenceContextBuilder(), provider  # type: ignore[arg-type]
    )
    answer = service.answer("What is the weather in Pune?")
    assert answer.evidence_state == "NO_VERIFIED_EVIDENCE"
    assert answer.assessment.state == "OUT_OF_DOMAIN"
    assert answer.metadata.gemini_call_avoided
    assert provider.calls == []


def test_partial_assessment_cannot_be_upgraded_by_provider():
    testing_only = result(text="Testing conditions are specified for the appliance.")
    provider = FakeLLMProvider([provider_response("VERIFIED_EVIDENCE")])
    service = GroundedAnswerService(
        Retrieval([testing_only]), EvidenceContextBuilder(), provider  # type: ignore[arg-type]
    )
    answer = service.answer("What testing and licensing conditions apply?")
    assert answer.assessment.state == "PARTIAL"
    assert answer.evidence_state == "PARTIAL_EVIDENCE"
    assert answer.limitations == ["The available BIS evidence supports only part of the question."]


def test_exact_identifier_behavior_and_answer_state_mapping_are_stable():
    assert answer_state_for_assessment("SUFFICIENT") == "VERIFIED_EVIDENCE"
    assert answer_state_for_assessment("PARTIAL") == "PARTIAL_EVIDENCE"
    assert answer_state_for_assessment("INSUFFICIENT") == "NO_VERIFIED_EVIDENCE"
    assert answer_state_for_assessment("OUT_OF_DOMAIN") == "NO_VERIFIED_EVIDENCE"
    assert assessment("IS 367", [result(score=-10.0, methods=("semantic",), bm25_rank=None)]).state == "SUFFICIENT"


def test_exact_identifier_does_not_verify_an_unrelated_predicate():
    weak = result(
        text="The appliance shall carry the Standard Mark.",
        context="IS 367:1993 | Marking",
        methods=("semantic",), bm25_rank=None, score=-8.0,
    )
    value = assessment("What is the weather in Pune under IS 367:1993?", [weak])
    assert value.state == "PARTIAL"
    assert value.reason == "exact_identifier_only_support"


def test_assessment_decision_and_signals_are_deterministic():
    source = package("What is the testing requirement for electric kettles?", [result()])
    first = EvidenceSufficiencyGuard().assess(source)
    second = EvidenceSufficiencyGuard().assess(source)
    assert first.model_dump(exclude={"assessment_ms"}) == second.model_dump(exclude={"assessment_ms"})
