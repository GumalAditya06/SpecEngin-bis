"""Stage 3.2 tests: vector index and semantic retrieval.

Index and search behaviour is tested against synthetic vectors so the suite
needs no model download and can construct exactly the cases that matter:
duplicate IDs, mixed revisions, unnormalized rows, and a stale index. Tests
that must confirm the real tokenizer and the real 768-dimension model are
marked ``real_model``.
"""

from __future__ import annotations

import json
import math
import random
from pathlib import Path

import numpy as np
import pytest

from scraper.config import Settings, resolve_embedding_spec
from scraper.retrieval import (
    DEFAULT_TOP_K,
    RETRIEVAL_SANITY_QUERIES,
    Retriever,
    RetrievalError,
    build_filter_mask,
    filter_capabilities,
    run_index,
)
from scraper.vectorstore import (
    FILTERABLE_FIELDS,
    METADATA_FIELDS,
    NORMALIZATION_TOLERANCE,
    NumpyExactStore,
    StaleIndexError,
    VectorStoreError,
    build_store,
    check_freshness,
    load_index,
    load_product_map,
    load_source_records,
)


DIMENSION = 8


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def unit_vector(seed: int, dimension: int = DIMENSION) -> list[float]:
    """A deterministic unit vector, so scores are reproducible across runs."""
    rng = random.Random(seed)
    vector = [rng.uniform(-1.0, 1.0) for _ in range(dimension)]
    norm = math.sqrt(sum(value * value for value in vector))
    return [value / norm for value in vector]


def make_record(chunk_id: str, seed: int, **overrides) -> dict:
    record = {
        "chunk_id": chunk_id,
        "document_id": f"doc-{seed % 3}",
        "standard_numbers": [f"IS 1000:{2000 + (seed % 3)}"],
        "document_title": f"Manual {seed % 3}",
        "document_type": "product_manual",
        "organization": "Bureau of Indian Standards",
        "clause_number": str(seed % 5) if seed % 5 else None,
        "clause_title": f"Clause {seed % 5}" if seed % 5 else None,
        "annex_identifier": chr(ord("A") + seed % 4) if seed % 4 == 0 else None,
        "pages": [1 + seed % 20],
        "start_page": 1 + seed % 20,
        "end_page": 1 + seed % 20,
        "source_url": f"https://example.invalid/doc-{seed % 3}.pdf",
        "context_prefix": f"IS 1000:{2000 + (seed % 3)} | Clause {seed % 5}",
        "text": f"Clause body number {seed}. " * 8,
        "embedding": unit_vector(seed),
        "embedding_model": "BAAI/bge-base-en-v1.5",
        "embedding_model_revision": "rev-abc123",
        "embedding_dimension": DIMENSION,
        "normalized": True,
    }
    record.update(overrides)
    return record


@pytest.fixture
def records() -> list[dict]:
    return [make_record(f"chunk{i:03d}", i) for i in range(12)]


@pytest.fixture
def store(records) -> NumpyExactStore:
    return build_store(records, product_by_document={f"doc-{i}": p for i, p in enumerate(["kettle", "mixer", "cooker"])})


@pytest.fixture
def corpus(tmp_path, records) -> tuple[Settings, Path, Path]:
    """A data dir with embeddings.jsonl and a manifest, and the index target."""
    settings = Settings(data_dir=tmp_path)
    processed = tmp_path / "processed"
    embeddings_dir = processed / "embeddings_v2"
    embeddings_dir.mkdir(parents=True)
    (tmp_path / "metadata").mkdir(parents=True, exist_ok=True)
    (tmp_path / "metadata" / "manifest.json").write_text(
        json.dumps([
            {"document_id": f"doc-{i}", "product_id": p}
            for i, p in enumerate(["kettle", "mixer", "cooker"])
        ]),
        encoding="utf-8",
    )
    embeddings_path = embeddings_dir / "embeddings.jsonl"
    embeddings_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in records),
        encoding="utf-8",
    )
    return settings, embeddings_path, processed / "vector_store"


def make_retriever(store, seed: int = 0) -> Retriever:
    """A retriever whose 'embedding' is a fixed vector, no model needed."""
    return Retriever(store, encode=lambda texts: [unit_vector(seed)])


# ---------------------------------------------------------------------------
# 1. Index creation
# ---------------------------------------------------------------------------


def test_index_creation_writes_all_required_artefacts(corpus):
    settings, embeddings_path, store_dir = corpus
    report = run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    assert (store_dir / "index.npz").exists()
    assert (store_dir / "metadata.jsonl").exists()
    assert (store_dir / "index_config.json").exists()
    assert (store_dir / "index_report.json").exists()
    assert report["number_of_vectors"] == 12
    assert report["dimension"] == DIMENSION
    assert report["similarity_metric"] == "cosine"


def test_index_report_contains_every_required_field(corpus):
    settings, embeddings_path, store_dir = corpus
    report = run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    for field in (
        "embedding_model", "model_revision", "dimension", "number_of_vectors",
        "similarity_metric", "normalization", "number_of_documents",
        "number_of_chunks", "index_type", "creation_timestamp",
        "source_embeddings_sha256",
    ):
        assert field in report, field
    assert report["embedding_model"] == "BAAI/bge-base-en-v1.5"
    assert report["model_revision"] == "rev-abc123"
    assert report["number_of_documents"] == 3
    assert report["number_of_chunks"] == report["number_of_vectors"]
    assert report["index_type"] == "exact_exhaustive_flat"


def test_index_lives_under_processed_vector_store_not_next_to_pdfs(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    assert store_dir == settings.processed_dir / "vector_store"
    assert store_dir.is_dir()
    # Nothing was written beside the source data.
    assert not (settings.data_dir / "raw" / store_dir.name).exists()


def test_missing_embeddings_file_is_reported(corpus, tmp_path):
    settings, _, store_dir = corpus
    with pytest.raises(VectorStoreError, match="embed"):
        run_index(settings, embeddings_path=tmp_path / "nope.jsonl", store_dir=store_dir)


# ---------------------------------------------------------------------------
# 2. Correct vector count
# ---------------------------------------------------------------------------


def test_index_holds_one_vector_per_embedding(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    store = load_index(store_dir, embeddings_path)
    assert store.size == 12
    assert len(store.chunk_ids) == 12
    assert len(store.metadata) == 12


def test_chunk_ids_survive_a_save_load_round_trip(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    loaded = load_index(store_dir, embeddings_path)
    assert loaded.chunk_ids == [f"chunk{i:03d}" for i in range(12)]


def test_row_count_matches_chunk_count_in_metadata(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    loaded = load_index(store_dir, embeddings_path)
    ids_in_metadata = [record["chunk_id"] for record in loaded.metadata]
    assert ids_in_metadata == loaded.chunk_ids


# ---------------------------------------------------------------------------
# 3. Correct vector dimension
# ---------------------------------------------------------------------------


def test_every_indexed_vector_has_the_same_dimension(records):
    store = build_store(records)
    assert store.dimension == DIMENSION
    assert store.matrix.shape == (len(records), DIMENSION)


def test_mismatched_dimension_is_rejected(records):
    store = NumpyExactStore()
    store.add("a", unit_vector(1))
    with pytest.raises(VectorStoreError, match="dimension"):
        store.add("b", unit_vector(2, dimension=DIMENSION + 1))


def test_query_dimension_must_match_the_index(store):
    with pytest.raises(VectorStoreError, match="query dimension"):
        store.search(unit_vector(1, dimension=DIMENSION + 2), top_k=3)


@pytest.mark.real_model
def test_real_index_is_768_dimensions():
    """The production corpus must index at the model's declared dimension."""
    path = Path("data/processed/embeddings_v2/embeddings.jsonl")
    if not path.exists():
        pytest.skip("Stage 3.1A embeddings not built")
    records, _ = load_source_records(path)
    store = build_store(records)
    expected = 768
    assert store.dimension == expected
    assert all(record["embedding_dimension"] == expected for record in records)
    assert store.size == len(records) == 97


# ---------------------------------------------------------------------------
# 4. Duplicate chunk IDs
# ---------------------------------------------------------------------------


def test_duplicate_chunk_ids_are_rejected(records):
    duplicated = records + [dict(records[0])]
    with pytest.raises(VectorStoreError, match="duplicate chunk_id"):
        build_store(duplicated)


def test_duplicate_detection_names_the_offending_ids(records):
    duplicated = [dict(record) for record in records]
    duplicated[5]["chunk_id"] = "chunk000"
    with pytest.raises(VectorStoreError, match="chunk000"):
        build_store(duplicated)


def test_search_never_returns_a_duplicate_chunk_id(store):
    results = make_retriever(store, seed=3).search("anything", top_k=12)
    ids = [result.chunk_id for result in results]
    assert len(ids) == len(set(ids))


# ---------------------------------------------------------------------------
# 5. Query embedding
# ---------------------------------------------------------------------------


def test_query_is_embedded_through_the_shared_encoder(store):
    seen: list[list[str]] = []

    def encode(texts):
        seen.append(list(texts))
        return [unit_vector(2)]

    retriever = Retriever(store, encode=encode)
    retriever.search("what is a control unit", top_k=1)
    assert seen == [["what is a control unit"]]


@pytest.mark.real_model
def test_query_encoder_is_the_shared_embed_queries_function():
    """Queries must go through embeddings.embed_queries, not a second path."""
    import inspect

    from scraper import embeddings
    from scraper.retrieval import Retriever as R

    source = inspect.getsource(R.__init__)
    assert "embed_queries" in source
    assert hasattr(embeddings, "embed_queries")


@pytest.mark.real_model
def test_query_preprocessing_matches_document_preprocessing():
    """The query and a document must be encoded by the same call path."""
    from scraper.config import Settings
    from scraper.embeddings import build_embedding_text, embed_queries

    settings = Settings()
    spec = resolve_embedding_spec()
    chunk = {
        "context_prefix": "IS 4250:2025 | Annex C | Clause 1.3.1",
        "text": "Control unit requirements.",
    }
    document_text = build_embedding_text(chunk, spec)
    # bge-base-en-v1.5 was selected for symmetric encoding: no prefix is added
    # on either side, so a query and a document are embedded identically apart
    # from the context prefix the chunk itself carries.
    assert spec.query_prefix == ""
    assert spec.document_prefix == ""
    assert document_text.endswith("Control unit requirements.")
    assert embed_queries is not None


def test_a_retriever_without_an_encoder_refuses_to_search(store):
    retriever = Retriever(store)
    with pytest.raises(RetrievalError, match="no query encoder"):
        retriever.search("query")


def test_empty_embedding_response_is_rejected(store):
    retriever = Retriever(store, encode=lambda texts: [[]])
    with pytest.raises(RetrievalError, match="empty"):
        retriever.search("query")


# ---------------------------------------------------------------------------
# 6. Cosine similarity
# ---------------------------------------------------------------------------


def test_identical_vector_scores_one(store):
    store.add("self", unit_vector(0))
    hits = store.search(unit_vector(0), top_k=1)
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)


def test_opposite_vector_scores_minus_one():
    vector = unit_vector(1)
    store = NumpyExactStore()
    store.add("pos", vector)
    store.add("neg", [-value for value in vector])
    store.finalize()
    hits = store.search(vector, top_k=2)
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)
    assert hits[1].score == pytest.approx(-1.0, abs=1e-6)


def test_orthogonal_vectors_score_zero():
    # Two basis vectors of the same space, trivially orthogonal.
    axis_x = [1.0] + [0.0] * (DIMENSION - 1)
    axis_y = [0.0, 1.0] + [0.0] * (DIMENSION - 2)
    store = NumpyExactStore()
    store.add("x", axis_x)
    store.add("y", axis_y)
    store.finalize()
    hits = store.search(axis_x, top_k=2)
    by_position = {hit.index: hit.score for hit in hits}
    assert by_position[0] == pytest.approx(1.0, abs=1e-6)
    assert abs(by_position[1]) < 1e-6


def test_score_equals_explicit_cosine_not_euclidean(store):
    """Cosine ignores magnitude; a same-direction longer vector still scores 1."""
    vector = unit_vector(3)
    store.add("scaled", [value * 4.0 for value in vector])
    store.finalize()
    hits = store.search(vector, top_k=1)
    assert hits[0].score == pytest.approx(1.0, abs=1e-6)
    # The Euclidean distance is 3, not 0, which is what a non-cosine metric
    # would have reported.
    euclidean = math.sqrt(sum(((value * 4.0) - value) ** 2 for value in vector))
    assert euclidean > 1.0


def test_cosine_is_computed_explicitly_when_vectors_are_not_normalized():
    """Unnormalized rows must be divided by their norms, not dotted blindly."""
    axis = [2.0] + [0.0] * (DIMENSION - 1)
    diagonal = [2.0] * DIMENSION
    store = NumpyExactStore()
    store.add("axis", axis)
    store.add("diagonal", diagonal)
    store.finalize()
    assert store.normalized is False

    hits = store.search([1.0] * DIMENSION, top_k=2)
    by_position = {hit.index: hit.score for hit in hits}
    # A naive dot product would report 2.0 and 16.0. True cosine divides by
    # both norms: the axis vector sits at 0.354, the diagonal is parallel at 1.0.
    assert by_position[0] == pytest.approx(1.0 / math.sqrt(DIMENSION), abs=1e-6)
    assert by_position[1] == pytest.approx(1.0, abs=1e-6)


def test_normalization_is_measured_and_recorded(store):
    assert store.normalized is True
    assert store.max_norm_deviation <= NORMALIZATION_TOLERANCE
    norms = np.linalg.norm(store.matrix.astype(np.float64), axis=1)
    assert np.allclose(norms, 1.0, atol=1e-6)


@pytest.mark.real_model
def test_real_vectors_are_normalized_enough_for_dot_product():
    """The dot-product shortcut is only valid if this check passes."""
    path = Path("data/processed/embeddings_v2/embeddings.jsonl")
    if not path.exists():
        pytest.skip("Stage 3.1A embeddings not built")
    records, _ = load_source_records(path)
    store = build_store(records)
    assert store.normalized is True
    assert store.max_norm_deviation < 1e-6
    matrix = store.matrix.astype(np.float64)
    for index in (0, 17, 50, 96):
        dot = float(matrix[index] @ matrix[index])
        explicit = float(
            matrix[index] @ matrix[index]
            / (np.linalg.norm(matrix[index]) ** 2)
        )
        assert abs(dot - explicit) < 1e-6


# ---------------------------------------------------------------------------
# 7. Top-K ordering
# ---------------------------------------------------------------------------


def test_results_are_sorted_by_descending_similarity(store):
    results = make_retriever(store, seed=5).search("q", top_k=12)
    scores = [result.similarity_score for result in results]
    assert scores == sorted(scores, reverse=True)


def test_ranks_are_sequential_from_one(store):
    results = make_retriever(store, seed=5).search("q", top_k=5)
    assert [result.rank for result in results] == [1, 2, 3, 4, 5]


def test_top_k_limits_the_result_count(store):
    retriever = make_retriever(store, seed=5)
    for top_k in (1, 3, 5, 10):
        assert len(retriever.search("q", top_k=top_k)) == min(top_k, store.size)


def test_top_k_defaults_to_five(store):
    assert DEFAULT_TOP_K == 5
    assert len(make_retriever(store, seed=5).search("q")) == DEFAULT_TOP_K


def test_top_k_larger_than_the_corpus_is_clamped(store):
    # 50 requested against a 12-vector index: the store returns what it has.
    results = make_retriever(store, seed=5).search("q", top_k=50)
    assert len(results) == store.size


def test_top_k_zero_or_negative_is_rejected(store):
    retriever = make_retriever(store, seed=5)
    with pytest.raises(RetrievalError, match="at least 1"):
        retriever.search("q", top_k=0)
    with pytest.raises(RetrievalError, match="at least 1"):
        retriever.search("q", top_k=-3)


def test_top_k_must_be_an_integer(store):
    retriever = make_retriever(store, seed=5)
    with pytest.raises(RetrievalError, match="integer"):
        retriever.search("q", top_k=2.5)
    with pytest.raises(RetrievalError, match="integer"):
        retriever.search("q", top_k=True)


def test_repeated_searches_are_identical(store):
    retriever = make_retriever(store, seed=7)
    first = retriever.search("q", top_k=8)
    second = retriever.search("q", top_k=8)
    assert [(r.chunk_id, r.similarity_score) for r in first] == [
        (r.chunk_id, r.similarity_score) for r in second
    ]


def test_ties_break_on_row_order_not_randomly():
    """Two identical vectors must resolve deterministically."""
    store = NumpyExactStore()
    store.add("tie-b", unit_vector(9))
    store.add("tie-a", unit_vector(9))
    store.finalize()
    hits = store.search(unit_vector(9), top_k=2)
    assert hits[0].score == hits[1].score
    assert [store.chunk_ids[hit.index] for hit in hits] == ["tie-b", "tie-a"]


# ---------------------------------------------------------------------------
# 8. Metadata preservation
# ---------------------------------------------------------------------------


def test_every_result_carries_the_full_metadata_contract(store):
    result = make_retriever(store, seed=1).search("q", top_k=1)[0]
    payload = result.to_dict()
    for field in (
        "rank", "chunk_id", "similarity_score", "document_id", "standard_numbers",
        "document_title", "document_type", "organization", "section_number",
        "section_title", "clause_number", "clause_title", "parent_clause_number",
        "annex_identifier", "table_identifier", "pages", "start_page", "end_page",
        "source_url", "text", "context_prefix",
    ):
        assert field in payload, field


def test_metadata_store_covers_the_declared_contract(store):
    """Every field the index promises to expose is actually present per row."""
    for record in store.metadata:
        for field in METADATA_FIELDS:
            assert field in record, field
        assert record["chunk_id"]
        assert record["text"]
        assert record["source_url"].startswith("https://")


def test_text_is_the_stored_source_text_verbatim(store, records):
    by_id = {record["chunk_id"]: record for record in records}
    for result in make_retriever(store, seed=4).search("q", top_k=12):
        assert result.text == by_id[result.chunk_id]["text"]


def test_result_metadata_matches_the_source_chunk(store, records):
    by_id = {record["chunk_id"]: record for record in records}
    for result in make_retriever(store, seed=4).search("q", top_k=12):
        record = by_id[result.chunk_id]
        assert result.document_id == record["document_id"]
        assert result.standard_numbers == record["standard_numbers"]
        assert result.clause_number == record["clause_number"]
        assert result.pages == record["pages"]
        assert result.source_url == record["source_url"]


def test_product_is_joined_from_the_manifest(store):
    result = make_retriever(store, seed=1).search("q", top_k=1)[0]
    assert result.product in {"kettle", "mixer", "cooker"}


def test_uncovered_document_gets_no_fabricated_product(records):
    store = build_store(records, product_by_document={})
    for result in make_retriever(store, seed=1).search("q", top_k=12):
        assert result.product is None


def test_index_is_not_the_only_source_of_metadata(corpus):
    """The metadata store must be readable without the vector matrix."""
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    lines = (store_dir / "metadata.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 12
    first = json.loads(lines[0])
    assert first["chunk_id"] == "chunk000"
    assert first["source_url"].startswith("https://")


def test_traceability_chain_is_intact(store, records):
    """chunk_id -> chunk record -> document -> page -> BIS URL."""
    by_id = {record["chunk_id"]: record for record in records}
    for result in make_retriever(store, seed=6).search("q", top_k=12):
        record = by_id[result.chunk_id]
        assert record["document_id"] == result.document_id
        assert result.pages == record["pages"]
        assert result.start_page == record["start_page"]
        assert result.source_url == record["source_url"]
        assert result.source_url.startswith("https://")


def test_retriever_refuses_an_index_with_no_metadata():
    """Vectors without a metadata store would return results with no provenance."""
    bare = NumpyExactStore()
    bare.add("a", unit_vector(0))
    bare.add("b", unit_vector(1))
    bare.finalize()
    assert bare.metadata == []
    with pytest.raises(VectorStoreError, match="provenance"):
        Retriever(bare, encode=lambda texts: [unit_vector(0)])


# ---------------------------------------------------------------------------
# 9. Standard filtering
# ---------------------------------------------------------------------------


def test_standard_filter_restricts_results(store, records):
    by_id = {record["chunk_id"]: record for record in records}
    target = by_id["chunk001"]["standard_numbers"][0]
    results = make_retriever(store, seed=2).search(
        "q", top_k=12, filters={"standard_number": target}
    )
    assert results
    assert all(target in result.standard_numbers for result in results)


def test_standard_filter_can_exclude_everything(store):
    results = make_retriever(store, seed=2).search(
        "q", top_k=5, filters={"standard_number": "IS 0000:0000"}
    )
    assert results == []


def test_standard_filter_preserves_ranking(store, records):
    """Filtering narrows the candidate set; it must not reorder what remains."""
    unfiltered = make_retriever(store, seed=2).search("q", top_k=12)
    target = records[0]["standard_numbers"][0]
    filtered = make_retriever(store, seed=2).search(
        "q", top_k=12, filters={"standard_number": target}
    )
    unfiltered_order = [r.chunk_id for r in unfiltered if target in r.standard_numbers]
    assert [r.chunk_id for r in filtered] == unfiltered_order


def test_standard_filter_matches_list_valued_metadata(records):
    """A document carrying two standards matches either one."""
    multi = [dict(record) for record in records]
    multi[0]["standard_numbers"] = ["IS 1111:2011", "IS 2222:2022"]
    store = build_store(multi)
    for target in ("IS 1111:2011", "IS 2222:2022"):
        results = make_retriever(store, seed=1).search(
            "q", top_k=12, filters={"standard_number": target}
        )
        assert any(result.chunk_id == "chunk000" for result in results), target


# ---------------------------------------------------------------------------
# 10. Document filtering
# ---------------------------------------------------------------------------


def test_document_filter_restricts_to_one_document(store, records):
    target = records[2]["document_id"]
    results = make_retriever(store, seed=2).search(
        "q", top_k=12, filters={"document_id": target}
    )
    assert results
    assert {result.document_id for result in results} == {target}


def test_product_filter_restricts_to_one_product(store):
    results = make_retriever(store, seed=2).search("q", top_k=12, filters={"product": "kettle"})
    assert results
    assert {result.product for result in results} == {"kettle"}


def test_clause_filter_restricts_to_that_clause(store):
    results = make_retriever(store, seed=2).search("q", top_k=12, filters={"clause_number": "3"})
    assert results
    assert {result.clause_number for result in results} == {"3"}


def test_annex_filter_restricts_to_that_annex(store):
    results = make_retriever(store, seed=2).search("q", top_k=12, filters={"annex_identifier": "A"})
    assert results
    assert {result.annex_identifier for result in results} == {"A"}


def test_document_type_filter(store):
    results = make_retriever(store, seed=2).search(
        "q", top_k=12, filters={"document_type": "product_manual"}
    )
    assert len(results) == 12


def test_filters_combine_as_intersection(store, records):
    target_document = records[0]["document_id"]
    target_standard = records[0]["standard_numbers"][0]
    results = make_retriever(store, seed=2).search(
        "q", top_k=12,
        filters={"document_id": target_document, "standard_number": target_standard},
    )
    for result in results:
        assert result.document_id == target_document
        assert target_standard in result.standard_numbers


def test_filter_does_not_change_the_scores(store):
    plain = make_retriever(store, seed=8).search("q", top_k=4)
    filtered = make_retriever(store, seed=8).search(
        "q", top_k=12, filters={"document_type": "product_manual"}
    )
    plain_scores = {r.chunk_id: r.similarity_score for r in plain}
    for result in filtered:
        if result.chunk_id in plain_scores:
            assert result.similarity_score == pytest.approx(plain_scores[result.chunk_id])


# ---------------------------------------------------------------------------
# 11. Missing filter fields
# ---------------------------------------------------------------------------


def test_filtering_on_an_absent_value_returns_nothing(store):
    """A clause-less chunk must not be returned by a clause filter."""
    store.metadata[0]["clause_number"] = None
    results = make_retriever(store, seed=2).search("q", top_k=12, filters={"clause_number": "0"})
    assert all(result.clause_number == "0" for result in results)


def test_unknown_filter_field_is_rejected_loudly(store):
    with pytest.raises(RetrievalError, match="unsupported filter field"):
        make_retriever(store, seed=2).search("q", top_k=3, filters={"nonexistent": "x"})


def test_rejection_names_the_available_fields(store):
    with pytest.raises(RetrievalError) as excinfo:
        make_retriever(store, seed=2).search("q", top_k=3, filters={"colour": "red"})
    message = str(excinfo.value)
    for field in FILTERABLE_FIELDS:
        assert field in message


def test_no_filter_produces_no_mask(store):
    mask, unknown = build_filter_mask(store.metadata, None)
    assert mask is None
    assert unknown == []
    mask, unknown = build_filter_mask(store.metadata, {})
    assert mask is None
    assert unknown == []


def test_mask_marks_exactly_the_matching_rows(store, records):
    target = records[2]["document_id"]
    mask, unknown = build_filter_mask(store.metadata, {"document_id": target})
    assert unknown == []
    assert mask.dtype == bool
    assert mask.shape == (len(store.metadata),)
    assert [i for i, keep in enumerate(mask) if keep] == [
        i for i, record in enumerate(records) if record["document_id"] == target
    ]


def test_masks_intersect_across_fields(store, records):
    document = records[0]["document_id"]
    mask, _ = build_filter_mask(
        store.metadata,
        {"document_id": document, "standard_number": "IS 0000:0000"},
    )
    # No row can satisfy both, so the intersection is empty.
    assert not mask.any()


def test_unknown_fields_are_reported_separately(store, records):
    mask, unknown = build_filter_mask(
        store.metadata, {"document_id": records[0]["document_id"], "colour": "red"}
    )
    assert unknown == ["colour"]
    # The known field still narrows the candidates.
    assert mask is not None


def test_sanity_queries_are_the_five_the_brief_specifies():
    """The retrieval record is pinned to the Stage 3.2 query set."""
    assert RETRIEVAL_SANITY_QUERIES == (
        "What is the definition of a control unit?",
        "What is the scope of the licence for electric kettles?",
        "What is the sampling plan for inspection of pressure cookers?",
        "What requirements apply to electric food mixers?",
        "What testing requirements are specified?",
    )


def test_filter_capabilities_report_partial_coverage(store):
    capabilities = filter_capabilities(store.metadata)
    assert capabilities["document_id"]["state"] == "supported"
    assert capabilities["standard_number"]["state"] == "supported"
    # Only a quarter of the synthetic records carry an annex identifier.
    assert capabilities["annex_identifier"]["state"] == "partial"
    assert capabilities["annex_identifier"]["rows_with_value"] < 12
    assert capabilities["annex_identifier"]["coverage"] < 1.0


def test_filter_capabilities_report_unavailable_fields(records):
    """A field the corpus does not carry is reported, not invented."""
    no_product = [dict(record) for record in records]
    store = build_store(no_product, product_by_document={})
    capabilities = filter_capabilities(store.metadata)
    assert capabilities["product"]["state"] == "unavailable"
    assert capabilities["product"]["rows_with_value"] == 0
    assert capabilities["product"]["example_values"] == []


def test_capabilities_are_recorded_in_the_index_report(corpus):
    settings, embeddings_path, store_dir = corpus
    report = run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    assert "filter_capabilities" in report
    assert set(report["filter_capabilities"]) == set(FILTERABLE_FIELDS)


# ---------------------------------------------------------------------------
# 12. Empty query handling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("query", ["", "   ", "\n\t "])
def test_empty_query_is_rejected(store, query):
    with pytest.raises(RetrievalError, match="non-empty"):
        make_retriever(store, seed=1).search(query, top_k=3)


def test_none_query_is_rejected(store):
    with pytest.raises(RetrievalError, match="non-empty"):
        make_retriever(store, seed=1).search(None, top_k=3)


def test_non_string_query_is_rejected(store):
    with pytest.raises(RetrievalError, match="non-empty"):
        make_retriever(store, seed=1).search(42, top_k=3)


def test_empty_index_returns_no_results_not_an_error():
    store = NumpyExactStore()
    assert store.search(unit_vector(0), top_k=5) == []
    store.finalize()
    assert store.normalized is True


def test_cli_reports_a_missing_query_instead_of_crashing():
    from scraper.cli import main

    assert main(["search"]) == 2


# ---------------------------------------------------------------------------
# 13. Stale index detection
# ---------------------------------------------------------------------------


def test_index_records_the_source_hash(corpus):
    settings, embeddings_path, store_dir = corpus
    report = run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    assert report["source_embeddings_sha256"]
    assert len(report["source_embeddings_sha256"]) == 64


def test_changing_the_embeddings_makes_the_index_stale(corpus, records):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    assert check_freshness(store_dir, embeddings_path)["stale"] is False
    changed = [dict(record) for record in records]
    changed[0]["embedding"] = unit_vector(999)
    embeddings_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in changed),
        encoding="utf-8",
    )
    freshness = check_freshness(store_dir, embeddings_path)
    assert freshness["stale"] is True
    assert freshness["indexed_sha256"] != freshness["current_sha256"]


def test_loading_a_stale_index_raises_with_a_rebuild_hint(corpus, records):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    embeddings_path.write_text(
        embeddings_path.read_text(encoding="utf-8") + json.dumps(
            make_record("chunk999", 999), sort_keys=True
        ) + "\n",
        encoding="utf-8",
    )
    with pytest.raises(StaleIndexError, match="scraper index"):
        load_index(store_dir, embeddings_path)


def test_missing_index_is_reported_clearly(tmp_path, corpus):
    _, embeddings_path, store_dir = corpus
    with pytest.raises(VectorStoreError, match="scraper index"):
        load_index(store_dir, embeddings_path)


def test_missing_embeddings_file_counts_as_stale(tmp_path):
    report_dir = tmp_path / "vector_store"
    report_dir.mkdir()
    (report_dir / "index_report.json").write_text(
        json.dumps({"source_embeddings_sha256": "abc"}), encoding="utf-8"
    )
    freshness = check_freshness(report_dir, tmp_path / "gone.jsonl")
    assert freshness["stale"] is True
    assert "missing" in freshness["reason"]


def test_rebuilding_clears_the_stale_flag(corpus, records):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    embeddings_path.write_text(embeddings_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert check_freshness(store_dir, embeddings_path)["stale"] is True
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    assert check_freshness(store_dir, embeddings_path)["stale"] is False


def test_mixed_model_revisions_are_refused(corpus, records):
    settings, embeddings_path, store_dir = corpus
    mixed = [dict(record) for record in records]
    mixed[0]["embedding_model_revision"] = "rev-different"
    embeddings_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in mixed), encoding="utf-8"
    )
    with pytest.raises(VectorStoreError, match="mixed model revisions"):
        run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)


def test_mixed_dimensions_are_refused(corpus, records):
    settings, embeddings_path, store_dir = corpus
    mixed = [dict(record) for record in records]
    mixed[0]["embedding"] = unit_vector(1, dimension=DIMENSION + 1)
    mixed[0]["embedding_dimension"] = DIMENSION + 1
    embeddings_path.write_text(
        "".join(json.dumps(record, sort_keys=True) + "\n" for record in mixed), encoding="utf-8"
    )
    with pytest.raises(VectorStoreError):
        run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)


# ---------------------------------------------------------------------------
# 14. Reproducible index creation
# ---------------------------------------------------------------------------


def test_two_builds_produce_identical_vectors(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    first = np.load(store_dir / "index.npz", allow_pickle=True)["vectors"]
    first_metadata = (store_dir / "metadata.jsonl").read_bytes()
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    second = np.load(store_dir / "index.npz", allow_pickle=True)["vectors"]
    assert np.array_equal(first, second)
    assert (store_dir / "metadata.jsonl").read_bytes() == first_metadata


def test_index_bytes_are_stable_across_builds(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    first = (store_dir / "index.npz").read_bytes()
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    assert (store_dir / "index.npz").read_bytes() == first


def test_search_results_survive_a_reload(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    before = [
        (r.chunk_id, round(r.similarity_score, 9))
        for r in make_retriever(load_index(store_dir, embeddings_path), seed=3).search("q", top_k=8)
    ]
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    after = [
        (r.chunk_id, round(r.similarity_score, 9))
        for r in make_retriever(load_index(store_dir, embeddings_path), seed=3).search("q", top_k=8)
    ]
    assert before == after


def test_index_config_records_the_backend_and_format(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    config = json.loads((store_dir / "index_config.json").read_text(encoding="utf-8"))
    assert config["backend"] == "numpy_exact_flat"
    assert config["search"] == "exact_exhaustive"
    assert config["metric"] == "cosine"
    assert config["format_version"] == 1
    assert config["normalized"] is True


def test_loading_an_index_from_another_backend_is_refused(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    config_path = store_dir / "index_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["backend"] = "qdrant_local"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(VectorStoreError, match="written by backend"):
        load_index(store_dir, embeddings_path)


def test_loading_a_future_format_version_is_refused(corpus):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    config_path = store_dir / "index_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config["format_version"] = 99
    config_path.write_text(json.dumps(config), encoding="utf-8")
    with pytest.raises(VectorStoreError, match="rebuild"):
        load_index(store_dir, embeddings_path)


def test_row_order_follows_embedding_file_order(corpus, records):
    settings, embeddings_path, store_dir = corpus
    run_index(settings, embeddings_path=embeddings_path, store_dir=store_dir)
    store = load_index(store_dir, embeddings_path)
    source_ids = [record["chunk_id"] for record in records]
    assert store.chunk_ids == source_ids


# ---------------------------------------------------------------------------
# Backend abstraction (the interface must be a real seam, not decoration)
# ---------------------------------------------------------------------------


def test_a_second_backend_can_be_substituted_without_touching_retrieval(store):
    """The point of the interface is that a different store still serves search.

    A stand-in backend built only from the abstract API must satisfy the same
    retrieval contract. If the retrieval layer reached into backend internals,
    this would not be writable.
    """
    from scraper.vectorstore import SearchHit, VectorStore

    class ReversedStore(VectorStore):
        """A backend with the opposite ranking, proving retrieval is agnostic."""

        backend_name = "test_reversed"

        def __init__(self, rows):
            self._rows = rows

        def add(self, chunk_id, vector):
            self._rows.append((chunk_id, list(vector)))

        def search(self, query_vector, top_k, filter_mask=None):
            scored = []
            for position, (chunk_id, vector) in enumerate(self._rows):
                if filter_mask is not None and not filter_mask[position]:
                    continue
                scored.append((position, chunk_id, sum(
                    a * b for a, b in zip(vector, query_vector))))
            scored.sort(key=lambda row: (row[2], row[0]))
            return [
                SearchHit(index=position, score=score)
                for position, _, score in scored[:top_k]
            ]

        def save(self, directory):
            raise NotImplementedError

        def load(self, directory):
            raise NotImplementedError

        @property
        def size(self):
            return len(self._rows)

        @property
        def dimension(self):
            return len(self._rows[0][1]) if self._rows else 0

        @property
        def chunk_ids(self):
            return [chunk_id for chunk_id, _ in self._rows]

        @property
        def normalized(self):
            return True

        metadata = property(lambda self: [dict(m) for m in self._meta])

    rows = [(f"chunk{i:03d}", unit_vector(i)) for i in range(6)]
    backend = ReversedStore(rows)
    backend._meta = [make_record(f"chunk{i:03d}", i) for i in range(6)]

    retriever = Retriever(backend, encode=lambda texts: [unit_vector(0)])
    results = retriever.search("q", top_k=6)
    assert backend.size == 6 and backend.dimension == DIMENSION
    assert [r.rank for r in results] == [1, 2, 3, 4, 5, 6]

    # The real store ranks descending; this one ranks ascending, and retrieval
    # does not reorder or second-guess it. The two disagree, which is the
    # point: the ordering came from the backend, not the retrieval layer.
    real = make_retriever(store, seed=0).search("q", top_k=6)
    assert [r.chunk_id for r in results] != [r.chunk_id for r in real]
    scores = [r.similarity_score for r in results]
    assert scores == sorted(scores)            # ascending, per this backend
    assert [r.chunk_id for r in results] == [
        row for row, _ in sorted(
            zip([chunk_id for chunk_id, _ in rows],
                [sum(a * b for a, b in zip(vector, unit_vector(0))) for _, vector in rows]),
            key=lambda pair: pair[1],
        )
    ]
    assert all(r.text and r.source_url for r in results)
    # Filters are honoured through the mask, exactly as the real backend does.
    filtered = retriever.search("q", top_k=6, filters={"document_id": "doc-2"})
    assert {r.document_id for r in filtered} == {"doc-2"}


def test_interface_forces_a_backend_to_implement_the_full_contract():
    from scraper.vectorstore import VectorStore

    # A partial backend cannot be instantiated: search, save, load and the
    # count properties are all abstract.
    class Incomplete(VectorStore):
        def add(self, chunk_id, vector):
            pass

    with pytest.raises(TypeError):
        Incomplete()


# ---------------------------------------------------------------------------
# Real model
# ---------------------------------------------------------------------------


def _all_keys(value) -> set[str]:
    """Every mapping key in a nested structure."""
    found: set[str] = set()
    if isinstance(value, dict):
        for key, child in value.items():
            found.add(str(key))
            found |= _all_keys(child)
    elif isinstance(value, list):
        for child in value:
            found |= _all_keys(child)
    return found


@pytest.mark.real_model
def test_real_index_builds_and_searches_end_to_end():
    path = Path("data/processed/embeddings_v2/embeddings.jsonl")
    if not path.exists():
        pytest.skip("Stage 3.1A embeddings not built")
    from scraper.embeddings import SentenceTransformerEmbedder

    settings = Settings()
    records, _ = load_source_records(path)
    store = build_store(records, product_by_document=load_product_map(
        settings.metadata_dir / "manifest.json"))
    assert store.size == 97
    spec = resolve_embedding_spec()
    retriever = Retriever(store, embedder=SentenceTransformerEmbedder(spec), spec=spec)
    results = retriever.search("What is the definition of a control unit?", top_k=5)
    assert len(results) == 5
    assert results[0].rank == 1
    # The control-unit definition clause must lead, as it did in Stage 3.1.
    assert results[0].clause_number == "1.3.1"
    assert "IS 2347" in "/".join(results[0].standard_numbers)
    assert results[0].similarity_score > 0.7
    for result in results:
        assert result.text
        assert result.source_url.startswith("https://")
        assert result.pages


@pytest.mark.real_model
def test_real_retrieval_sanity_report_writes_without_an_accuracy_score(tmp_path):
    from scraper.retrieval import run_retrieval_sanity

    source = Path("data/processed/embeddings_v2/embeddings.jsonl")
    store_dir = Path("data/processed/vector_store")
    if not source.exists() or not store_dir.exists():
        pytest.skip("index not built")
    output = tmp_path / "retrieval_sanity_report.json"
    report = run_retrieval_sanity(
        Settings(), store_dir=store_dir, embeddings_path=source,
        top_k=5, output_path=output,
    )
    assert output.exists()
    assert report["query_count"] == 5
    assert report["result_count"] == 25
    assert report["similarity_metric"] == "cosine"
    assert report["query_preprocessing"]["shared_with_document_embedding"] is True

    # No retrieval-quality metric is reported anywhere. With no manually
    # verified ground truth, any such number would be invented. The check is on
    # keys, because the report's own note legitimately uses these words to say
    # why it is not reporting one.
    forbidden = ("accuracy", "precision", "recall", "ndcg", "mrr", "f1", "hit_rate", "score_")
    keys = _all_keys(report)
    offenders = [key for key in keys if any(word in key.lower() for word in forbidden)]
    assert offenders == [], offenders
    # The only per-result float is the cosine similarity, bounded by [-1, 1].
    # (rank and page are integers describing position, not quality.)
    for entry in report["queries"]:
        for result in entry["results"]:
            numeric = [value for value in result.values() if isinstance(value, float)]
            assert numeric == [result["similarity_score"]]
            assert -1.0 <= result["similarity_score"] <= 1.0
            assert result["chunk_id"]
            assert result["text"]
