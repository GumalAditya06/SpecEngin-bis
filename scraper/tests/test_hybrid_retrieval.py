"""Stage 3.3 tests for BM25, RRF, reranking, and provenance."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from scraper.hybrid_retrieval import (
    BM25Index,
    DEFAULT_CANDIDATE_K,
    DEFAULT_FINAL_K,
    DEFAULT_RERANK_K,
    DEFAULT_RRF_K,
    HybridRetriever,
    build_reranker_text,
    build_searchable_text,
    reciprocal_rank_fusion,
)
from scraper.retrieval import RETRIEVAL_SANITY_QUERIES, Result, RetrievalError
from scraper.vectorstore import load_index


def record(chunk_id: str, text: str, **overrides) -> dict:
    value = {
        "chunk_id": chunk_id,
        "document_id": "doc-a",
        "document_type": "product_manual",
        "standard_numbers": ["IS 4250:2025"],
        "clause_number": "4",
        "clause_title": "Requirements",
        "annex_identifier": None,
        "pages": [3],
        "source_url": "https://example.invalid/a.pdf",
        "context_prefix": "IS 4250:2025 | Clause 4 — Requirements",
        "product": "mixer",
        "text": text,
    }
    value.update(overrides)
    return value


@pytest.fixture
def records():
    return [
        record("a", "A control unit controls the operating speed."),
        record(
            "b", "Scope of the licence for electric kettles.",
            standard_numbers=["IS 367:1993"], clause_number="1.3.1",
            context_prefix="IS 367:1993 | Clause 1.3.1 — Scope", product="kettle",
            document_id="doc-b", document_type="standard",
        ),
        record(
            "c", "Sampling plan for inspection of pressure cookers.",
            standard_numbers=["IS 2347:2023"], clause_number="7",
            context_prefix="IS 2347:2023 | Clause 7 — Sampling",
            product="pressure_cooker", document_id="doc-c", document_type="qco",
        ),
    ]


def semantic_result(source: dict, rank: int, score: float = 0.8) -> Result:
    return Result(
        rank=rank,
        chunk_id=source["chunk_id"],
        similarity_score=score,
        document_id=source.get("document_id"),
        standard_numbers=list(source.get("standard_numbers") or []),
        document_title=source.get("document_title"),
        document_type=source.get("document_type"),
        organization=source.get("organization"),
        section_number=source.get("section_number"),
        section_title=source.get("section_title"),
        clause_number=source.get("clause_number"),
        clause_title=source.get("clause_title"),
        parent_clause_number=source.get("parent_clause_number"),
        annex_identifier=source.get("annex_identifier"),
        table_identifier=source.get("table_identifier"),
        pages=list(source.get("pages") or []),
        start_page=(source.get("pages") or [None])[0],
        end_page=(source.get("pages") or [None])[-1],
        source_url=source.get("source_url"),
        text=source.get("text", ""),
        context_prefix=source.get("context_prefix"),
        product=source.get("product"),
    )


class FakeSemantic:
    def __init__(self, records, order=None):
        self.records = list(records)
        self.order = order or list(range(len(records)))
        self.store = SimpleNamespace(chunk_ids=[item["chunk_id"] for item in records])

    def search(self, query, top_k, filters=None):
        selected = []
        for index in self.order:
            item = self.records[index]
            if filters:
                matches = all(
                    (expected in item.get("standard_numbers", []))
                    if field == "standard_number"
                    else str(item.get(field)) == str(expected)
                    for field, expected in filters.items()
                )
                if not matches:
                    continue
            selected.append(item)
        return [
            semantic_result(item, rank, 1.0 - rank / 100)
            for rank, item in enumerate(selected[:top_k], start=1)
        ]


class FakeReranker:
    def __init__(self, scorer=None):
        self.scorer = scorer or (lambda document: float("control unit" in document.lower()))
        self.seen = []

    def score(self, query, documents):
        self.seen.append((query, list(documents)))
        return [self.scorer(document) for document in documents]


# 1. BM25 index creation
def test_bm25_index_creation(records):
    index = BM25Index(records)
    assert len(index.documents) == len(records)
    assert index.average_length > 0
    assert "4250:2025" in index.idf


def test_bm25_representation_indexes_only_documented_fields():
    item = record("a", "body", source_url="secret-token-url")
    searchable = build_searchable_text(item)
    assert "body" in searchable and "IS 4250:2025" in searchable
    assert "secret-token-url" not in searchable


# 2. BM25 search
def test_bm25_search_returns_score_rank_metadata_and_text(records):
    result = BM25Index(records).search("control unit", top_k=1)[0]
    assert result.chunk_id == "a"
    assert result.rank == 1 and result.bm25_score > 0
    assert result.metadata["pages"] == [3]
    assert result.text == records[0]["text"]


# 3. Empty query
def test_empty_query_is_rejected(records):
    with pytest.raises(RetrievalError, match="non-empty"):
        BM25Index(records).search("  ")


# 4. Exact standard-number retrieval
@pytest.mark.parametrize("query,expected", [
    ("IS 4250:2025", "a"), ("IS 367:1993", "b"), ("IS 2347:2023", "c")
])
def test_exact_standard_number_retrieval(records, query, expected):
    assert BM25Index(records).search(query, top_k=1)[0].chunk_id == expected


# 5. Exact clause-number retrieval
def test_exact_clause_number_retrieval(records):
    assert BM25Index(records).search("Clause 1.3.1", top_k=1)[0].chunk_id == "b"


# 6-7. Union and duplicate removal
def test_semantic_and_bm25_candidate_union_removes_duplicates(records):
    semantic = [semantic_result(records[0], 1), semantic_result(records[1], 2)]
    lexical = BM25Index(records).search("pressure cookers control unit", top_k=3)
    fused = reciprocal_rank_fusion(semantic, lexical)
    assert {item.chunk_id for item in fused} == {"a", "b", "c"}
    assert len(fused) == len({item.chunk_id for item in fused})


# 8. RRF calculation
def test_rrf_calculation_for_candidate_in_both_lists(records):
    semantic = [semantic_result(records[0], 1)]
    lexical = BM25Index(records).search("control unit", top_k=1)
    fused = reciprocal_rank_fusion(semantic, lexical, rrf_k=60)
    assert fused[0].fusion_score == pytest.approx(2 / 61)


# 9. RRF ranking
def test_rrf_ranks_dual_source_candidate_first(records):
    semantic = [semantic_result(records[0], 1), semantic_result(records[1], 2)]
    lexical = BM25Index(records).search("scope licence electric kettles", top_k=2)
    fused = reciprocal_rank_fusion(semantic, lexical)
    assert fused[0].chunk_id == "b"
    assert fused[0].semantic_rank == 2 and fused[0].bm25_rank == 1


# 10. Metadata filters
@pytest.mark.parametrize("field,value,expected", [
    ("standard_number", "IS 367:1993", "b"),
    ("document_id", "doc-b", "b"),
    ("document_type", "qco", "c"),
    ("product", "pressure_cooker", "c"),
    ("clause_number", "1.3.1", "b"),
    ("annex_identifier", "A", "c"),
])
def test_bm25_metadata_filters(records, field, value, expected):
    records[2]["annex_identifier"] = "A"
    results = BM25Index(records).search(
        "control scope sampling requirements", top_k=3, filters={field: value}
    )
    assert results and results[0].chunk_id == expected


def test_hybrid_applies_filters_before_reranking(records):
    reranker = FakeReranker()
    hybrid = HybridRetriever(FakeSemantic(records), BM25Index(records), reranker)
    results, timing = hybrid.search("requirements", filters={"product": "kettle"})
    assert [result.chunk_id for result in results] == ["b"]
    assert len(reranker.seen[0][1]) == 1
    assert timing["reranked_candidates"] == 1


# 11. Reranker input
def test_reranker_input_contains_context_and_exact_chunk_text(records):
    value = build_reranker_text(records[1])
    assert value.startswith(records[1]["context_prefix"])
    assert value.endswith(records[1]["text"])


# 12. Final ranking
def test_final_ranking_is_decided_by_reranker(records):
    hybrid = HybridRetriever(FakeSemantic(records), BM25Index(records), FakeReranker())
    results, timing = hybrid.search("requirements", final_k=2)
    assert results[0].chunk_id == "a"
    assert results[0].rank == 1 and len(results) == 2
    assert set(timing) >= {"bm25_ms", "semantic_ms", "fusion_ms", "reranking_ms", "total_ms"}


# 13. Provenance preservation
def test_final_result_preserves_provenance(records):
    hybrid = HybridRetriever(FakeSemantic(records), BM25Index(records), FakeReranker())
    result = hybrid.search("control unit", final_k=1)[0][0].to_dict()
    assert result["standard_numbers"] == ["IS 4250:2025"]
    assert result["clause_number"] == "4"
    assert result["pages"] == [3]
    assert result["text"] == records[0]["text"]
    assert result["source_url"] == records[0]["source_url"]


# 14. Existing vector store compatibility
@pytest.mark.real_model
def test_existing_vector_store_is_compatible_with_bm25_corpus():
    store = load_index(
        __import__("pathlib").Path("data/processed/vector_store"),
        __import__("pathlib").Path("data/processed/embeddings_v2/embeddings.jsonl"),
    )
    index = BM25Index.from_jsonl(
        __import__("pathlib").Path("data/processed/chunks_v2/chunks.jsonl"),
        metadata_records=store.metadata,
    )
    assert set(store.chunk_ids) == {item["chunk_id"] for item in index.records}
    assert len(index.records) == 97


# 15. Frozen query set
def test_frozen_five_query_set_is_unchanged():
    assert RETRIEVAL_SANITY_QUERIES == (
        "What is the definition of a control unit?",
        "What is the scope of the licence for electric kettles?",
        "What is the sampling plan for inspection of pressure cookers?",
        "What requirements apply to electric food mixers?",
        "What testing requirements are specified?",
    )


# 16. Deterministic behavior
def test_hybrid_ranking_is_deterministic(records):
    hybrid = HybridRetriever(FakeSemantic(records), BM25Index(records), FakeReranker())
    first = [item.to_dict() for item in hybrid.search("control unit")[0]]
    second = [item.to_dict() for item in hybrid.search("control unit")[0]]
    assert first == second


def test_default_configuration_matches_stage_brief():
    assert (DEFAULT_CANDIDATE_K, DEFAULT_RERANK_K, DEFAULT_FINAL_K, DEFAULT_RRF_K) == (20, 40, 5, 60)
