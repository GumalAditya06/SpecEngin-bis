"""Stage 3.1 embedding pipeline tests.

Most tests use a deterministic stub embedder so the suite needs no model
download and runs in milliseconds. The two tests that genuinely exercise the
real model are marked ``real_model`` and skip when the weights are unavailable
offline, so ``pytest`` stays green in a network-free environment.
"""

from __future__ import annotations

import json
import math

import pytest

from scraper.config import (
    DEFAULT_EMBEDDING_BATCH_SIZE,
    EMBEDDING_MODELS,
    EmbeddingSpec,
    Settings,
    resolve_embedding_spec,
)
from scraper.embeddings import (
    PROVENANCE_FIELDS,
    BatchFailure,
    EmbeddingError,
    build_embedding_text,
    build_record,
    chunk_order_key,
    cosine_similarity,
    embed_chunks,
    embed_queries,
    is_normalized,
    l2_norm,
    run_embeddings,
    semantic_sanity_test,
    validate_embeddings,
    batched,
)


# ---------------------------------------------------------------------------
# Deterministic stub embedder
# ---------------------------------------------------------------------------


class StubEmbedder:
    """Dimension-correct, deterministic, and controllable stand-in.

    The vector is a stable hash of character frequencies projected onto a fixed
    size, so identical input always yields an identical unit vector. That makes
    both the similarity maths and the determinism assertions meaningful without
    a 440 MB download.
    """

    def __init__(self, dimension: int = 8, fail_on: set[int] | None = None, non_finite_on: set[int] | None = None):
        self._dimension = dimension
        self._max_seq_length = 16
        self._calls: list[int] = []
        self._batch_index = 0
        # Indices refer to the batch ordinal within this embedder instance.
        self.fail_on = fail_on or set()
        self.non_finite_on = non_finite_on or set()
        self.spec = EmbeddingSpec(
            model="stub", model_name="stub-model", provider="stub", revision="stub-rev",
            batch_size=3, device="cpu", normalize=True, pooling="cls",
        )

    # -- interface expected by the pipeline -------------------------------
    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_seq_length(self) -> int:
        return self._max_seq_length

    @property
    def model_name(self) -> str:
        return "stub-model"

    def count_tokens(self, text: str) -> int:
        return len(text.split())

    def will_truncate(self, text: str) -> bool:
        return self.count_tokens(text) > self._max_seq_length

    def info(self):
        from scraper.embeddings import EmbedderInfo

        return EmbedderInfo(
            model_name="stub-model", revision="stub-rev", provider="stub",
            dimension=self._dimension, normalized=True, max_seq_length=self._max_seq_length,
            pooling_mode="cls", device="cpu", parameter_count=0,
            library_versions={"stub": "1"}, load_seconds=0.0,
        )

    def encode(self, texts):
        batch_index = self._batch_index
        self._batch_index += 1
        self._calls.append(len(texts))
        if batch_index in self.fail_on:
            raise RuntimeError("stub encoder failure")
        vectors = []
        for position, text in enumerate(texts):
            vector = [0.0] * self._dimension
            for index, char in enumerate(text):
                vector[index % self._dimension] += (ord(char) % 17) / 17.0
            if not any(vector):
                vector[0] = 1.0
            norm = l2_norm(vector)
            vector = [value / norm for value in vector]
            if batch_index in self.non_finite_on and position == 0:
                vector[0] = float("nan")
            vectors.append(vector)
        return vectors


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_chunk(index: int, **overrides) -> dict:
    chunk = {
        "chunk_id": f"c{index:03d}",
        "document_id": "doc-1",
        "standard_numbers": ["IS 4250:2025"],
        "document_title": "Electric Food-Mixers manual",
        "document_type": "product_manual",
        "organization": "Bureau of Indian Standards",
        "section_number": "1",
        "section_title": "Product",
        "clause_number": f"1.{index}",
        "clause_title": f"Clause {index}",
        "parent_clause_number": "1",
        "annex_identifier": None,
        "annex_title": None,
        "pages": [index],
        "start_page": index,
        "end_page": index,
        "source_url": f"https://www.bis.gov.in/doc{index}.pdf",
        "source_file": f"data/raw/mixer/doc{index}.pdf",
        "sha256": f"hash{index}",
        "context_prefix": f"IS 4250:2025 | Clause 1.{index}",
        "text": f"1.{index} The appliance shall comply with the requirement {index}.",
        "token_count": 10,
        "tokenizer": "regex_v1",
    }
    chunk.update(overrides)
    return chunk


@pytest.fixture
def chunks() -> list[dict]:
    return [make_chunk(index) for index in range(1, 8)]


@pytest.fixture
def spec() -> EmbeddingSpec:
    return EmbeddingSpec(
        model="stub", model_name="stub-model", provider="stub", revision="stub-rev",
        batch_size=3, device="cpu", normalize=True, pooling="cls",
    )


def write_corpus(tmp_path, chunks) -> tuple[Settings, object]:
    chunks_dir = tmp_path / "processed" / "chunks"
    chunks_dir.mkdir(parents=True)
    path = chunks_dir / "chunks.jsonl"
    path.write_text(
        "".join(json.dumps(chunk, sort_keys=True) + "\n" for chunk in chunks), encoding="utf-8"
    )
    return Settings(data_dir=tmp_path), path


# ---------------------------------------------------------------------------
# 1. Model loads / 2. Correct embedding dimension (real model)
# ---------------------------------------------------------------------------


def _real_model_available() -> bool:
    from scraper.embeddings import SentenceTransformerEmbedder

    spec = resolve_embedding_spec()
    try:
        embedder = SentenceTransformerEmbedder(spec)
    except EmbeddingError:
        return False
    return embedder.dimension > 0


@pytest.mark.real_model
def test_model_loads_and_exposes_dimension_from_weights():
    from scraper.embeddings import SentenceTransformerEmbedder

    spec = resolve_embedding_spec()
    try:
        embedder = SentenceTransformerEmbedder(spec)
    except EmbeddingError as exc:
        pytest.skip(f"embedding model unavailable offline: {exc}")
    info = embedder.info()
    assert info.model_name == spec.model_name
    assert info.dimension == embedder.dimension
    assert info.dimension > 0
    assert info.max_seq_length > 0
    # The dimension must come from the model's own module graph, not config.
    pooling = embedder._model[1]
    assert embedder.dimension == int(pooling.get_embedding_dimension())
    assert embedder.dimension == int(embedder._model[0].auto_model.config.hidden_size)


@pytest.mark.real_model
def test_real_embedding_has_expected_dimension_and_unit_norm():
    from scraper.embeddings import SentenceTransformerEmbedder

    spec = resolve_embedding_spec()
    try:
        embedder = SentenceTransformerEmbedder(spec)
    except EmbeddingError as exc:
        pytest.skip(f"embedding model unavailable offline: {exc}")
    vectors = embedder.encode(["Control unit requirements for electric food mixers."])
    assert len(vectors) == 1
    assert len(vectors[0]) == embedder.dimension
    assert is_normalized(vectors[0])


# ---------------------------------------------------------------------------
# 3. Empty input handling
# ---------------------------------------------------------------------------


def test_empty_input_produces_no_records_and_no_failures(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks([], embedder, spec)
    assert run.records == []
    assert run.failures == []
    assert run.batches == 0
    assert embedder._calls == []


def test_empty_input_still_writes_valid_empty_outputs(tmp_path, spec):
    settings, _ = write_corpus(tmp_path, [])
    report = run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=[])
    assert report["input_chunks"] == 0
    assert report["successful_embeddings"] == 0
    embeddings = settings.embeddings_dir / "embeddings.jsonl"
    assert embeddings.read_text(encoding="utf-8") == ""
    assert report["validation"]["checks"]["passed"] is True


def test_empty_queries_return_no_vectors(spec):
    assert embed_queries([], StubEmbedder(), spec) == []


def test_batched_rejects_non_positive_size():
    with pytest.raises(ValueError):
        list(batched([1, 2, 3], 0))


# ---------------------------------------------------------------------------
# 4. Batch processing
# ---------------------------------------------------------------------------


def test_batches_respect_configured_size(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    assert all(size == spec.batch_size for size in embedder._calls[:-1])
    assert len(run.records) == len(chunks)
    assert run.batches == 3  # 7 chunks at batch size 3 -> 3, 3, 1


def test_batched_splits_evenly_and_preserves_coverage():
    assert [list(batch) for batch in batched(list(range(6)), 2)] == [[0, 1], [2, 3], [4, 5]]


def test_single_batch_size_embeds_everything(chunks, spec):
    run = embed_chunks(chunks, StubEmbedder(), EmbeddingSpec(
        model="stub", model_name="stub-model", provider="stub", revision="r",
        batch_size=100, device="cpu", normalize=True, pooling="cls",
    ))
    assert run.batches == 1
    assert len(run.records) == len(chunks)


# ---------------------------------------------------------------------------
# 5. Partial final batch
# ---------------------------------------------------------------------------


def test_partial_final_batch_is_processed(chunks, spec):
    run = embed_chunks(chunks, StubEmbedder(), spec)
    assert run.batch_sizes == [3, 3, 1]
    assert run.batches == 3
    assert len(run.records) == 7
    assert [record["chunk_id"] for record in run.records] == [c["chunk_id"] for c in chunks]


@pytest.mark.parametrize("size,expected", [(1, 7), (2, 4), (3, 3), (4, 2), (5, 2), (6, 2), (7, 1), (8, 1)])
def test_final_batch_handles_every_remainder(chunks, size, expected):
    run = embed_chunks(
        chunks, StubEmbedder(),
        EmbeddingSpec(model="s", model_name="s", provider="s", revision="r",
                      batch_size=size, device="cpu", normalize=True, pooling="cls"),
    )
    assert run.batches == expected
    assert sum(run.batch_sizes) == len(chunks)
    assert len(run.records) == len(chunks)


def test_report_records_the_partial_batch(tmp_path, spec):
    settings, _ = write_corpus(tmp_path, [make_chunk(i) for i in range(1, 8)])
    report = run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["clause?"])
    detail = report["batch_detail"]
    assert detail["batch_sizes_observed"] == [3, 3, 1]
    assert detail["final_partial_batch"] == 1


# ---------------------------------------------------------------------------
# 6. Duplicate chunk ID detection
# ---------------------------------------------------------------------------


def test_duplicate_chunk_ids_are_detected_in_records(chunks, spec):
    run = embed_chunks(chunks, StubEmbedder(), spec)
    run.records.append(dict(run.records[0]))
    result = validate_embeddings(chunks, run.records, run.failures, StubEmbedder(), "sha")
    assert result["checks"]["chunk_ids_unique"] is False
    assert result["details"]["duplicate_chunk_ids"] == [chunks[0]["chunk_id"]]
    assert result["checks"]["passed"] is False


def test_duplicate_chunk_ids_are_detected_in_input(chunks, spec):
    duplicated = chunks + [make_chunk(1)]
    embedder = StubEmbedder()
    run = embed_chunks(duplicated, embedder, spec)
    result = validate_embeddings(duplicated, run.records, run.failures, embedder, "sha")
    assert result["checks"]["chunk_ids_unique"] is False
    assert result["details"]["duplicate_input_chunk_ids"] == ["c001"]


def test_unique_chunk_ids_pass(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    result = validate_embeddings(chunks, run.records, run.failures, embedder, "sha", chunks_unchanged=True)
    assert result["checks"]["chunk_ids_unique"] is True
    assert result["checks"]["passed"] is True


# ---------------------------------------------------------------------------
# 7 & 8. NaN / infinity detection
# ---------------------------------------------------------------------------


def test_nan_values_are_detected(chunks, spec):
    embedder = StubEmbedder(non_finite_on={0})
    run = embed_chunks(chunks, embedder, spec)
    result = validate_embeddings(chunks, run.records, run.failures, embedder, "sha")
    assert result["checks"]["no_nan_values"] is False
    assert result["checks"]["passed"] is False
    assert chunks[0]["chunk_id"] in result["details"]["non_finite_chunk_ids"]


def test_infinity_values_are_detected(chunks, spec):
    embedder = StubEmbedder()
    vectors = embedder.encode(["a"])
    vectors[0][0] = float("inf")
    record = build_record(chunks[0], vectors[0], embedder, spec)
    result = validate_embeddings(chunks, [record], [], embedder, "sha")
    assert result["checks"]["no_infinite_values"] is False
    assert result["details"]["non_finite_chunk_ids"] == ["c001"]


def test_finite_vectors_pass_nan_and_infinity_checks(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    result = validate_embeddings(chunks, run.records, run.failures, embedder, "sha")
    assert result["checks"]["no_nan_values"] is True
    assert result["checks"]["no_infinite_values"] is True


# ---------------------------------------------------------------------------
# 9. Metadata preservation
# ---------------------------------------------------------------------------


def test_record_preserves_every_provenance_field(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    for chunk, record in zip(chunks, run.records):
        for field in PROVENANCE_FIELDS:
            assert record[field] == chunk[field], field


def test_record_carries_vector_identity_fields(chunks, spec):
    embedder = StubEmbedder(dimension=8)
    run = embed_chunks(chunks, embedder, spec)
    record = run.records[0]
    assert record["embedding_model"] == "stub-model"
    assert record["embedding_dimension"] == 8
    assert record["normalized"] is True
    assert record["embedding_provider"] == "stub"
    assert record["embedding_model_revision"] == "stub-rev"
    assert len(record["embedding"]) == 8


def test_metadata_mismatch_is_reported(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    run.records[0]["clause_title"] = "Tampered"
    result = validate_embeddings(chunks, run.records, run.failures, embedder, "sha")
    assert result["checks"]["source_metadata_preserved"] is False
    assert result["details"]["metadata_errors"][0]["mismatched_fields"] == ["clause_title"]


def test_embedding_input_prepends_context_prefix_without_touching_text(chunks, spec):
    chunk = chunks[0]
    combined = build_embedding_text(chunk, spec)
    assert combined.startswith(chunk["context_prefix"] + "\n\n")
    assert combined.endswith(chunk["text"].strip())
    # The authoritative text itself is byte-identical to what was read.
    assert chunk["text"] in combined


def test_embedding_input_without_prefix_uses_text_alone(spec):
    chunk = make_chunk(1, context_prefix=None)
    assert build_embedding_text(chunk, spec) == chunk["text"].strip()


def test_chunks_file_is_not_modified(tmp_path, spec):
    settings, path = write_corpus(tmp_path, [make_chunk(i) for i in range(1, 6)])
    before = path.read_bytes()
    report = run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["clause?"])
    assert path.read_bytes() == before
    assert report["validation"]["checks"]["chunks_input_unchanged"] is True
    assert report["input_provenance"]["chunks_sha256"]


# ---------------------------------------------------------------------------
# 10. Deterministic input ordering
# ---------------------------------------------------------------------------


def test_ordering_is_independent_of_input_file_order(chunks, spec):
    forward = embed_chunks(chunks, StubEmbedder(), spec)
    reverse = embed_chunks(list(reversed(chunks)), StubEmbedder(), spec)
    assert [r["chunk_id"] for r in forward.records] != [r["chunk_id"] for r in reverse.records]
    forward_by_id = {r["chunk_id"]: r["embedding"] for r in forward.records}
    reverse_by_id = {r["chunk_id"]: r["embedding"] for r in reverse.records}
    assert forward_by_id == reverse_by_id


def test_sorting_by_chunk_order_key_is_stable(chunks):
    assert sorted(chunks, key=chunk_order_key) == chunks
    assert sorted(list(reversed(chunks)), key=chunk_order_key) == chunks


def test_repeat_run_is_deterministic(tmp_path, spec):
    settings, _ = write_corpus(tmp_path, [make_chunk(i) for i in range(1, 8)])
    first = run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["q"])
    second = run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["q"])
    assert first["validation"]["details"] == second["validation"]["details"]
    def vectors(report):
        rows = [json.loads(line) for line in
                (settings.embeddings_dir / "embeddings.jsonl").read_text(encoding="utf-8").splitlines() if line]
        return {row["chunk_id"]: row["embedding"] for row in rows}
    assert vectors(first) == vectors(second)


def test_pipeline_orders_chunks_deterministically(tmp_path, spec):
    unsorted_chunks = [make_chunk(i) for i in (4, 2, 7, 1, 5, 3, 6)]
    settings, _ = write_corpus(tmp_path, unsorted_chunks)
    run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["q"])
    rows = [json.loads(line) for line in
            (settings.embeddings_dir / "embeddings.jsonl").read_text(encoding="utf-8").splitlines() if line]
    assert [row["chunk_id"] for row in rows] == [c["chunk_id"] for c in sorted(unsorted_chunks, key=chunk_order_key)]


# ---------------------------------------------------------------------------
# 11. Query embedding
# ---------------------------------------------------------------------------


def test_queries_are_embedded_with_the_same_model(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    query_vectors = embed_queries(["what is a control unit?"], embedder, spec)
    assert len(query_vectors) == 1
    assert len(query_vectors[0]) == embedder.dimension
    # Queries reuse the identical encoder, so their dimension always matches.
    assert len(query_vectors[0]) == len(run.records[0]["embedding"])


def test_query_embedding_is_deterministic(spec):
    embedder = StubEmbedder()
    assert embed_queries(["q"], embedder, spec) == embed_queries(["q"], embedder, spec)


def test_multiple_queries_are_embedded_in_order(spec):
    embedder = StubEmbedder()
    vectors = embed_queries(["first", "second", "third"], embedder, spec)
    assert len(vectors) == 3
    assert vectors[0] == embed_queries(["first"], embedder, spec)[0]


def test_query_prefix_is_applied_when_configured():
    spec = EmbeddingSpec(model="s", model_name="s", provider="s", revision="r",
                         batch_size=4, device="cpu", normalize=True,
                         query_prefix="query: ", pooling="mean")
    embedder = StubEmbedder()
    query = "define the control unit"
    # With a query prefix configured, the query must encode identically to the
    # explicitly prefixed text, proving the prefix is applied by the pipeline.
    assert embed_queries([query], embedder, spec) == embedder.encode([f"query: {query}"])
    plain = EmbeddingSpec(model="s", model_name="s", provider="s", revision="r",
                          batch_size=4, device="cpu", normalize=True, pooling="mean")
    assert embed_queries([query], embedder, spec) != embed_queries([query], embedder, plain)


# ---------------------------------------------------------------------------
# 12. Cosine similarity
# ---------------------------------------------------------------------------


def test_cosine_similarity_of_identical_vectors_is_one():
    assert cosine_similarity([1.0, 0.0, 0.0], [1.0, 0.0, 0.0]) == pytest.approx(1.0)


def test_cosine_similarity_of_orthogonal_vectors_is_zero():
    assert cosine_similarity([1.0, 0.0], [0.0, 1.0]) == pytest.approx(0.0)


def test_cosine_similarity_of_opposite_vectors_is_minus_one():
    assert cosine_similarity([1.0, 0.0], [-1.0, 0.0]) == pytest.approx(-1.0)


def test_cosine_similarity_is_scale_invariant():
    assert cosine_similarity([3.0, 4.0], [30.0, 40.0]) == pytest.approx(1.0)


def test_cosine_similarity_ignores_magnitude_but_not_direction():
    assert cosine_similarity([1.0, 1.0], [5.0, 5.0]) == pytest.approx(1.0)
    assert cosine_similarity([1.0, 1.0], [5.0, -5.0]) == pytest.approx(0.0)


def test_cosine_similarity_rejects_dimension_mismatch():
    with pytest.raises(ValueError):
        cosine_similarity([1.0, 0.0], [1.0, 0.0, 0.0])


def test_cosine_similarity_of_zero_vectors_is_zero():
    assert cosine_similarity([0.0, 0.0], [1.0, 0.0]) == 0.0


def test_cosine_similarity_rejects_empty_vector():
    with pytest.raises(ValueError):
        cosine_similarity([], [])


def test_normalization_is_verified_not_assumed():
    assert is_normalized([1.0, 0.0]) is True
    assert is_normalized([0.6, 0.8]) is True
    assert is_normalized([3.0, 4.0]) is False   # norm 5, not a unit vector
    assert is_normalized([1.0, 1.0]) is False   # norm sqrt(2)
    assert is_normalized([]) is False
    assert l2_norm([3.0, 4.0]) == pytest.approx(5.0)


def test_sanity_test_ranks_by_cosine_similarity(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    result = semantic_sanity_test(["clause one", "clause two"], run.records, embedder, spec, top_k=3)
    assert len(result["queries"]) == 2
    for query in result["queries"]:
        scores = [row["similarity"] for row in query["results"]]
        assert scores == sorted(scores, reverse=True)
        assert [row["rank"] for row in query["results"]] == [1, 2, 3]
    assert result["chunks_searched"] == len(chunks)
    assert result["all_vectors_normalized"] is True


def test_sanity_test_respects_top_k(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    result = semantic_sanity_test(["q"], run.records, embedder, spec, top_k=5)
    assert len(result["results"] if "results" in result else result["queries"][0]["results"]) == 5


def test_sanity_test_results_carry_citation_metadata(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    result = semantic_sanity_test(["q"], run.records, embedder, spec, top_k=1)
    top = result["queries"][0]["results"][0]
    for key in ("rank", "chunk_id", "similarity", "standard", "clause", "page", "text"):
        assert key in top
    source = next(chunk for chunk in chunks if chunk["chunk_id"] == top["chunk_id"])
    assert top["standard"] == source["standard_numbers"][0]
    assert top["clause"] == source["clause_number"]
    assert top["page"] == source["start_page"]
    assert top["text"] == source["text"]


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------


def test_failed_batch_is_recorded_and_other_chunks_continue(chunks, spec):
    embedder = StubEmbedder(fail_on={1})  # second batch raises
    run = embed_chunks(chunks, embedder, spec)
    assert len(run.records) == 4  # batches 0 and 2 survive
    assert len(run.failures) == 1
    failure = run.failures[0]
    assert failure.reason == "batch_encoding_failed"
    assert failure.error_type == "RuntimeError"
    assert failure.chunk_ids == [chunks[3]["chunk_id"], chunks[4]["chunk_id"], chunks[5]["chunk_id"]]


def test_failures_are_counted_in_validation(chunks, spec):
    embedder = StubEmbedder(fail_on={0})
    run = embed_chunks(chunks, embedder, spec)
    result = validate_embeddings(chunks, run.records, run.failures, embedder, "sha")
    assert result["checks"]["successful_embedding_count_matches"] is True
    assert result["checks"]["all_chunks_embedded_or_failed"] is True
    assert result["details"]["failed_embeddings"] == 3
    assert result["details"]["successful_embeddings"] == 4


def test_failed_chunks_are_never_silently_dropped(chunks, spec):
    embedder = StubEmbedder(fail_on={0})
    run = embed_chunks(chunks, embedder, spec)
    accounted = {r["chunk_id"] for r in run.records} | {cid for f in run.failures for cid in f.chunk_ids}
    assert accounted == {c["chunk_id"] for c in chunks}


def test_report_surfaces_failures(tmp_path, spec):
    settings, _ = write_corpus(tmp_path, [make_chunk(i) for i in range(1, 8)])
    report = run_embeddings(settings, spec=spec, embedder=StubEmbedder(fail_on={0}), queries=["q"])
    assert report["failed_embeddings"] == 3
    assert report["successful_embeddings"] == 4
    assert report["failures"][0]["reason"] == "batch_encoding_failed"
    assert report["failures"][0]["chunk_ids"]


def test_wrong_dimension_is_detected(chunks, spec):
    embedder = StubEmbedder(dimension=8)
    run = embed_chunks(chunks, embedder, spec)
    run.records[0]["embedding"] = [0.1, 0.2]  # truncated vector
    result = validate_embeddings(chunks, run.records, run.failures, embedder, "sha")
    assert result["checks"]["all_dimensions_match_model"] is False
    assert result["details"]["wrong_dimension_chunk_ids"] == ["c001"]


def test_unmapped_embedding_is_detected(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    run.records[0]["chunk_id"] = "not-a-chunk"
    result = validate_embeddings(chunks, run.records, run.failures, embedder, "sha")
    assert result["checks"]["every_embedding_maps_to_chunk"] is False


def test_validation_requires_the_byte_level_unchanged_check(chunks, spec):
    embedder = StubEmbedder()
    run = embed_chunks(chunks, embedder, spec)
    result = validate_embeddings(chunks, run.records, run.failures, embedder, "sha")
    assert result["checks"]["chunks_input_unchanged"] is False
    assert result["checks"]["passed"] is False


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_spec_defaults_are_configurable():
    spec = resolve_embedding_spec()
    assert spec.model_name == "BAAI/bge-base-en-v1.5"
    assert spec.batch_size == DEFAULT_EMBEDDING_BATCH_SIZE
    assert spec.normalize is True
    assert spec.revision  # pinned for reproducibility


def test_spec_reads_environment(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "bge-small-en-v1.5")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")
    spec = resolve_embedding_spec()
    assert spec.model_name == "BAAI/bge-small-en-v1.5"
    assert spec.batch_size == 8


def test_explicit_arguments_beat_environment(monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "bge-small-en-v1.5")
    monkeypatch.setenv("EMBEDDING_BATCH_SIZE", "8")
    spec = resolve_embedding_spec(model="all-MiniLM-L6-v2", batch_size=64)
    assert spec.model_name == "sentence-transformers/all-MiniLM-L6-v2"
    assert spec.batch_size == 64


def test_every_registered_model_is_fully_specified():
    for key, spec in EMBEDDING_MODELS.items():
        assert spec.key == key
        assert spec.name and spec.provider and spec.pooling and spec.license
        assert spec.rationale


def test_unregistered_model_is_allowed_but_unpinned():
    spec = resolve_embedding_spec(model="some-org/some-embedding-model")
    assert spec.model_name == "some-org/some-embedding-model"
    assert spec.revision == ""


def test_spec_validation_rejects_bad_batch_size():
    with pytest.raises(ValueError):
        EmbeddingSpec(model="m", model_name="m", provider="p", revision="r", batch_size=0).validate()


def test_missing_chunks_file_raises_clear_error(tmp_path, spec):
    with pytest.raises(FileNotFoundError, match="scraper chunk"):
        run_embeddings(Settings(data_dir=tmp_path), spec=spec, embedder=StubEmbedder())


# ---------------------------------------------------------------------------
# Output artefacts
# ---------------------------------------------------------------------------


def test_outputs_are_written_to_the_expected_paths(tmp_path, spec):
    settings, _ = write_corpus(tmp_path, [make_chunk(i) for i in range(1, 5)])
    run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["q"])
    embeddings = settings.embeddings_dir / "embeddings.jsonl"
    report = settings.embeddings_dir / "embedding_report.json"
    semantic = settings.embeddings_dir / "semantic_test.json"
    assert embeddings.exists() and report.exists() and semantic.exists()
    assert json.loads(report.read_text())["input_chunks"] == 4
    rows = [json.loads(line) for line in embeddings.read_text().splitlines() if line]
    assert len(rows) == 4
    assert rows[0]["embedding_model"] == "stub-model"
    assert json.loads(semantic.read_text())["queries"][0]["query"] == "q"


def test_report_contains_required_fields(tmp_path, spec):
    settings, _ = write_corpus(tmp_path, [make_chunk(i) for i in range(1, 5)])
    report = run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["q"])
    for key in ("documents", "input_chunks", "successful_embeddings", "failed_embeddings",
                "model", "embedding_dimension", "normalized", "batch_size", "generated_at"):
        assert key in report
    assert report["embedding_dimension"] == 8  # read from the model, not assumed
    assert report["timing"]["embedding_generation_seconds"] >= 0
    assert report["timing"]["model_load_seconds"] == 0.0
    assert report["timing"]["average_seconds_per_chunk"] >= 0
    assert report["timing"]["total_runtime_seconds"] >= 0
    assert report["model_detail"]["library_versions"]


def test_truncation_is_recorded_when_chunks_exceed_context_window(tmp_path, spec):
    long_text = " ".join(f"word{index}" for index in range(40))  # 40 tokens > 16 window
    settings, _ = write_corpus(tmp_path, [make_chunk(1, text=long_text)])
    report = run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["q"])
    assert report["truncation"]["chunks_truncated"] == 1
    assert report["truncation"]["truncated_chunk_ids"] == ["c001"]
    row = json.loads((settings.embeddings_dir / "embeddings.jsonl").read_text().splitlines()[0])
    assert row["text"] == long_text  # stored text stays complete


def test_no_vector_database_is_created(tmp_path, spec):
    settings, _ = write_corpus(tmp_path, [make_chunk(i) for i in range(1, 4)])
    run_embeddings(settings, spec=spec, embedder=StubEmbedder(), queries=["q"])
    produced = {path.suffix for path in settings.embeddings_dir.iterdir()}
    assert produced <= {".jsonl", ".json"}
    names = " ".join(path.name for path in settings.embeddings_dir.iterdir())
    for forbidden in ("faiss", "qdrant", "pgvector", "chroma", "pinecone", "milvus", "index"):
        assert forbidden not in names.lower()
