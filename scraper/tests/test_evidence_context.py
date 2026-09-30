"""Stage 4.1 tests for deterministic, provider-neutral evidence context."""

from __future__ import annotations

from dataclasses import replace

import pytest
from pydantic import ValidationError

from scraper.evidence_context import (
    DEFAULT_MAX_CONTEXT_CHARS,
    EvidenceContextBuilder,
    EvidenceContextError,
    EvidencePackage,
)
from scraper.hybrid_retrieval import (
    DEFAULT_BM25_B,
    DEFAULT_BM25_K1,
    DEFAULT_CANDIDATE_K,
    DEFAULT_FINAL_K,
    DEFAULT_RERANK_K,
    DEFAULT_RRF_K,
    RERANKER_MODEL,
    RERANKER_REVISION,
)
from scraper.retrieval_service import (
    EvidenceResult,
    EvidenceRetrieval,
    EvidenceSource,
)


def result(rank: int = 1, chunk_id: str = "chunk-a", **overrides) -> EvidenceResult:
    values = {
        "rank": rank,
        "chunk_id": chunk_id,
        "standard_number": "IS 367:1993",
        "clause_number": "4.2",
        "clause_title": "Construction",
        "text": "The appliance shall meet the specified requirement.",
        "context_prefix": "IS 367:1993 | Clause 4.2 — Construction",
        "source": EvidenceSource(
            document_id="doc-a",
            source_id="source-a",
            title="Electric kettles and jugs",
            version="1993",
            page=12,
            url="https://example.invalid/is-367.pdf",
        ),
        "retrieval": EvidenceRetrieval(
            methods=("semantic", "bm25"),
            semantic_rank=2,
            bm25_rank=1,
            rrf_score=0.031,
            reranker_score=4.2,
        ),
    }
    values.update(overrides)
    return EvidenceResult(**values)


def test_normal_evidence_construction():
    package = EvidenceContextBuilder().build("kettle requirements", [result()])
    item = package.evidence[0]
    assert item.evidence_id == "E1"
    assert item.chunk_id == "chunk-a"
    assert item.standard == "IS 367:1993"
    assert item.document_id == "doc-a"
    assert item.retrieval_methods == ("semantic", "bm25")
    assert package.evidence_count == 1 and package.has_evidence is True


def test_multiple_items_get_deterministic_rank_ids():
    package = EvidenceContextBuilder().build(
        "requirements",
        [result(), result(2, "chunk-b"), result(3, "chunk-c")],
    )
    assert [item.evidence_id for item in package.evidence] == ["E1", "E2", "E3"]
    assert [item.chunk_id for item in package.evidence] == ["chunk-a", "chunk-b", "chunk-c"]


def test_authoritative_text_is_preserved_exactly():
    text = "  First line.\n\nSecond line with µg and Ω.  \n"
    item = EvidenceContextBuilder().build("unicode", [result(text=text)]).evidence[0]
    assert item.authoritative_text == text


def test_context_prefix_is_preserved_exactly():
    prefix = "  IS 367:1993 | Clause 4.2\nAnnex note  "
    package = EvidenceContextBuilder().build("context", [result(context_prefix=prefix)])
    assert package.evidence[0].context_prefix == prefix
    assert f"Context:\n{prefix}\n\nAuthoritative Text:" in package.context_text


@pytest.mark.parametrize(
    "field,value",
    [
        ("standard_number", None),
        ("clause_number", None),
        ("clause_title", None),
        ("context_prefix", None),
    ],
)
def test_nullable_top_level_provenance_is_preserved(field, value):
    package = EvidenceContextBuilder().build("nullable", [result(**{field: value})])
    item = package.evidence[0]
    mapped = "standard" if field == "standard_number" else field
    assert getattr(item, mapped) is None
    assert "Unavailable" in package.context_text


@pytest.mark.parametrize("field", ["source_id", "version", "page", "url"])
def test_nullable_source_provenance_is_preserved(field):
    source = replace(result().source, **{field: None})
    item = EvidenceContextBuilder().build("nullable", [result(source=source)]).evidence[0]
    assert getattr(item, field) is None


def test_empty_results_are_a_valid_empty_package():
    package = EvidenceContextBuilder().build("no match", [])
    assert package.evidence == []
    assert package.evidence_count == 0
    assert package.has_evidence is False
    assert package.context_text == ""
    assert package.omitted_evidence == [] and package.omitted_count == 0


def test_non_evidence_result_is_rejected():
    with pytest.raises(EvidenceContextError, match="only EvidenceResult"):
        EvidenceContextBuilder().build("invalid type", [{"chunk_id": "x"}])  # type: ignore[list-item]


def test_duplicate_chunk_ids_are_rejected():
    with pytest.raises(EvidenceContextError, match="duplicate chunk_id"):
        EvidenceContextBuilder().build("duplicate", [result(), result(2, "chunk-a")])


@pytest.mark.parametrize("rank", [0, -1, True])
def test_invalid_ranks_are_rejected(rank):
    with pytest.raises(EvidenceContextError, match="positive integer"):
        EvidenceContextBuilder().build("rank", [result(rank=rank)])


@pytest.mark.parametrize(
    "results",
    [
        [result(2, "chunk-b")],
        [result(1, "chunk-a"), result(3, "chunk-c")],
        [result(2, "chunk-b"), result(1, "chunk-a")],
    ],
)
def test_noncontiguous_or_reordered_ranks_are_rejected(results):
    with pytest.raises(EvidenceContextError, match="contiguous rank order"):
        EvidenceContextBuilder().build("rank order", results)


@pytest.mark.parametrize("text", ["", "  ", "\n\t"])
def test_missing_authoritative_text_is_rejected(text):
    with pytest.raises(EvidenceContextError, match="authoritative_text is required"):
        EvidenceContextBuilder().build("missing text", [result(text=text)])


def test_context_generation_is_deterministic():
    results = [result(), result(2, "chunk-b")]
    builder = EvidenceContextBuilder()
    first = builder.build("same query", results).model_dump(mode="json")
    second = builder.build("same query", results).model_dump(mode="json")
    assert first == second


def test_context_contains_all_ids_in_retrieval_order():
    package = EvidenceContextBuilder().build(
        "ordered", [result(), result(2, "chunk-b"), result(3, "chunk-c")]
    )
    positions = [package.context_text.index(f"[E{rank}]") for rank in (1, 2, 3)]
    assert positions == sorted(positions)


def test_context_format_distinguishes_context_from_authoritative_text():
    package = EvidenceContextBuilder().build("format", [result()])
    assert package.context_text.startswith("[E1]\nStandard: IS 367:1993")
    assert "\nContext:\nIS 367:1993 | Clause 4.2 — Construction" in package.context_text
    assert "\nAuthoritative Text:\nThe appliance shall" in package.context_text
    assert "None" not in package.context_text


def test_unicode_is_preserved_in_structured_and_formatted_context():
    text = "तापमान 100 °C से अधिक नहीं होना चाहिए — परीक्षण Ω."
    package = EvidenceContextBuilder().build("तापमान", [result(text=text)])
    assert package.evidence[0].authoritative_text == text
    assert text in package.context_text


def test_single_oversized_item_is_omitted_whole_without_truncation():
    evidence = result(text="A" * 500)
    complete = EvidenceContextBuilder().build("long", [evidence])
    budget = len(complete.context_text) - 1
    package = EvidenceContextBuilder(max_context_chars=budget).build("long", [evidence])
    assert package.evidence == [] and package.context_text == ""
    assert package.omitted_count == 1
    assert package.omitted_evidence[0].evidence_id == "E1"
    assert package.omitted_evidence[0].reason == "context_budget_exceeded"


def test_budget_keeps_highest_ranked_whole_prefix_only():
    first = result(text="First authoritative text.")
    second = result(2, "chunk-b", text="Second authoritative text.")
    first_context = EvidenceContextBuilder().build("budget", [first]).context_text
    package = EvidenceContextBuilder(max_context_chars=len(first_context)).build(
        "budget", [first, second]
    )
    assert [item.evidence_id for item in package.evidence] == ["E1"]
    assert [item.evidence_id for item in package.omitted_evidence] == ["E2"]
    assert package.evidence[0].authoritative_text == first.text
    assert second.text not in package.context_text


def test_default_context_budget_is_explicit_and_respected():
    package = EvidenceContextBuilder().build("default", [result()])
    assert package.max_context_chars == DEFAULT_MAX_CONTEXT_CHARS
    assert len(package.context_text) <= DEFAULT_MAX_CONTEXT_CHARS


@pytest.mark.parametrize("budget", [0, -1, True, 1.5])
def test_invalid_context_budget_is_rejected(budget):
    with pytest.raises(EvidenceContextError, match="positive integer"):
        EvidenceContextBuilder(max_context_chars=budget)


def test_no_provenance_is_fabricated():
    source = EvidenceSource(None, None, None, None, None, None)
    evidence = result(
        standard_number=None,
        clause_number=None,
        clause_title=None,
        context_prefix=None,
        source=source,
    )
    item = EvidenceContextBuilder().build("unknown metadata", [evidence]).evidence[0]
    assert item.standard is None
    assert item.clause_number is None and item.clause_title is None
    assert item.context_prefix is None
    assert all(
        value is None
        for value in (item.document_id, item.source_id, item.title, item.version, item.page, item.url)
    )


def test_package_model_rejects_inconsistent_counts():
    with pytest.raises(ValidationError, match="evidence_count"):
        EvidencePackage(
            query="invalid",
            evidence=[],
            evidence_count=1,
            has_evidence=False,
            context_text="",
            max_context_chars=100,
            omitted_count=0,
        )


def test_builder_does_not_mutate_stage34_results():
    evidence = result()
    before = evidence.to_dict()
    EvidenceContextBuilder().build("immutable", [evidence])
    assert evidence.to_dict() == before


def test_stage33_and_stage34_retrieval_configuration_remains_frozen():
    assert (DEFAULT_BM25_K1, DEFAULT_BM25_B) == (1.5, 0.75)
    assert (DEFAULT_CANDIDATE_K, DEFAULT_RERANK_K, DEFAULT_FINAL_K, DEFAULT_RRF_K) == (
        20,
        40,
        5,
        60,
    )
    assert RERANKER_MODEL == "cross-encoder/ms-marco-MiniLM-L6-v2"
    assert RERANKER_REVISION == "233902d25c440f23af6f7d6e94d2946bac0bee0a"
