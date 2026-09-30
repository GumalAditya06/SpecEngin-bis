"""Stage 3.1: chunk text -> embedding vectors.

This module is the first retrieval component in the pipeline. It reads the
frozen Stage 2.5 output (``chunks.jsonl``), builds one deterministic embedding
input per chunk, and writes embedding records plus provenance-validated
reports.

Scope boundaries enforced here:

* ``chunks.jsonl`` is read-only. The run aborts if its bytes change.
* The authoritative chunk text is never rewritten, summarised, or cleaned. The
  only transformation applied to the embedded string is prepending the
  already-recorded ``context_prefix``; both strings are stored back verbatim.
* No vector database, no retrieval API, no answer generation. The similarity
  search below is a sanity probe, not a product surface.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import time
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Sequence

from .config import (
    DEFAULT_SEMANTIC_TEST_TOP_K,
    SEMANTIC_TEST_QUERIES,
    EmbeddingSpec,
    Settings,
    resolve_embedding_spec,
)
from .utils import atomic_json, utc_now

# Provenance fields that must survive from the chunk into the embedding record.
# Order is the report's canonical field order and must stay readable.
PROVENANCE_FIELDS: tuple[str, ...] = (
    "chunk_id",
    "document_id",
    "standard_numbers",
    "document_title",
    "document_type",
    "organization",
    "section_number",
    "section_title",
    "clause_number",
    "clause_title",
    "parent_clause_number",
    "annex_identifier",
    "annex_title",
    "pages",
    "start_page",
    "end_page",
    "source_url",
    "source_file",
    "sha256",
    "context_prefix",
    "text",
)

# Additional chunk attributes carried through untouched for traceability.
PASSTHROUGH_FIELDS: tuple[str, ...] = (
    "structural_type",
    "structural_path",
    "table_identifier",
    "table_title",
    "table_header_context",
    "chunk_index",
    "chunk_count",
    "token_count",
    "context_token_count",
    "retrieval_token_count",
    "overlap_tokens",
    "tokenizer",
    "structured_input_file",
    "structured_input_sha256",
    "metadata_conflict",
    "conflict_type",
    "verification_status",
)

# Fields describing the vector itself, written into every record.
VECTOR_FIELDS: tuple[str, ...] = (
    "embedding_model",
    "embedding_model_revision",
    "embedding_provider",
    "embedding_dimension",
    "normalized",
    "embedding_pooling",
    "embedding",
)


class EmbeddingError(RuntimeError):
    """Raised when the embedding model cannot be loaded at all."""


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def batched(items: Sequence, size: int) -> Iterator[list]:
    """Yield consecutive batches of at most ``size`` items.

    The final batch is emitted even when it is shorter than ``size``; an empty
    input yields nothing at all. This is the only place batch boundaries are
    decided, so corpus and query paths cannot drift apart.
    """
    if size < 1:
        raise ValueError("batch size must be at least 1")
    for start in range(0, len(items), size):
        yield list(items[start : start + size])


# ---------------------------------------------------------------------------
# Embedding input construction
# ---------------------------------------------------------------------------


def embedding_prefix(chunk: dict, spec: EmbeddingSpec | None = None) -> str:
    """The structural context prepended to a chunk before embedding.

    A table that had to be split is uninterpretable without its column
    headings, so the recorded ``table_header_context`` leads the prefix. The
    header is carried in its own field and is never merged into ``text``, which
    stays an exact substring of the source.
    """
    parts: list[str] = []
    header = str(chunk.get("table_header_context") or "").strip()
    if header:
        parts.append(header)
    prefix = str(chunk.get("context_prefix") or "").strip()
    if spec is not None and spec.document_prefix:
        prefix = f"{spec.document_prefix} {prefix}".strip() if prefix else spec.document_prefix
    if prefix:
        parts.append(prefix)
    return "\n\n".join(parts)


def build_embedding_text(chunk: dict, spec: EmbeddingSpec) -> str:
    """Compose the exact string that is embedded for one chunk.

    The layout is the structural prefix (and table header when present), a
    blank line, then the untouched source text. Prefixing the structural
    breadcrumb is what lets a bare clause body retrieve under queries that name
    the standard, annex, or clause heading. The source ``text`` is never
    altered; only this derived string differs.
    """
    text = str(chunk.get("text") or "")
    prefix = embedding_prefix(chunk, spec)
    body = text.strip()
    return f"{prefix}\n\n{body}" if prefix else body


# ---------------------------------------------------------------------------
# Model wrapper
# ---------------------------------------------------------------------------


@dataclass
class EmbedderInfo:
    model_name: str
    revision: str
    provider: str
    dimension: int
    normalized: bool
    max_seq_length: int
    pooling_mode: str
    device: str
    parameter_count: int
    library_versions: dict
    load_seconds: float

    def as_dict(self) -> dict:
        return {
            "model": self.model_name,
            "model_revision": self.revision,
            "provider": self.provider,
            "embedding_dimension": self.dimension,
            "normalized": self.normalized,
            "max_seq_length": self.max_seq_length,
            "pooling_mode": self.pooling_mode,
            "device": self.device,
            "parameter_count": self.parameter_count,
            "license": None,
            "load_seconds": round(self.load_seconds, 3),
            "library_versions": self.library_versions,
        }


class SentenceTransformerEmbedder:
    """Thin, testable wrapper around ``sentence_transformers``.

    The dimension is read from the loaded model rather than from configuration.
    Nothing here assumes 768 or any other size.
    """

    def __init__(self, spec: EmbeddingSpec):
        self.spec = spec
        try:
            import sentence_transformers
            import torch
            import transformers
        except ImportError as exc:  # pragma: no cover - environment guard
            raise EmbeddingError(
                "sentence-transformers, torch and transformers are required for "
                "`scraper embed` and `--tokenizer bge`. Install them with: "
                ".venv/bin/python -m pip install sentence-transformers torch"
            ) from exc
        self._torch = torch
        started = time.perf_counter()
        kwargs: dict = {"device": spec.device}
        if spec.revision:
            kwargs["revision"] = spec.revision
        try:
            self._model = sentence_transformers.SentenceTransformer(spec.model_name, **kwargs)
        except Exception as exc:  # noqa: BLE001 - surfaced as a clear pipeline error
            raise EmbeddingError(f"could not load embedding model {spec.model_name!r}: {exc}") from exc
        self._load_seconds = time.perf_counter() - started
        # Read the real dimension from the model, never from configuration.
        # get_embedding_dimension is the current name; the older accessor is
        # kept as a fallback so this works across sentence-transformers 3.x-6.x.
        reader = getattr(self._model, "get_embedding_dimension", None) or (
            self._model.get_sentence_embedding_dimension
        )
        self._dimension = int(reader())
        self._max_seq_length = int(getattr(self._model, "max_seq_length", 0) or 0)
        self._parameter_count = int(sum(p.numel() for p in self._model.parameters()))
        self._tokenizer = self._model.tokenizer
        self._library_versions = {
            "sentence_transformers": sentence_transformers.__version__,
            "transformers": transformers.__version__,
            "torch": torch.__version__,
            "python": platform.python_version(),
        }

    @property
    def dimension(self) -> int:
        return self._dimension

    @property
    def max_seq_length(self) -> int:
        return self._max_seq_length

    @property
    def model_name(self) -> str:
        return self.spec.model_name

    @property
    def tokenizer(self):
        """The model's real tokenizer. Token accounting must use this."""
        return self._tokenizer

    @property
    def parameter_count(self) -> int:
        return self._parameter_count

    @property
    def library_versions(self) -> dict:
        return dict(self._library_versions)

    def count_tokens(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False))

    def input_tokens(self, text: str) -> int:
        """Token length the model actually receives, special tokens included."""
        return len(self._tokenizer.encode(text, add_special_tokens=True))

    def will_truncate(self, text: str) -> bool:
        return bool(self._max_seq_length) and self.input_tokens(text) > self._max_seq_length

    def info(self) -> EmbedderInfo:
        return EmbedderInfo(
            model_name=self.spec.model_name,
            revision=self.spec.revision,
            provider=self.spec.provider,
            dimension=self._dimension,
            normalized=self.spec.normalize,
            max_seq_length=self._max_seq_length,
            pooling_mode=self.spec.pooling,
            device=self.spec.device,
            parameter_count=self._parameter_count,
            library_versions=dict(self._library_versions),
            load_seconds=self._load_seconds,
        )

    def encode(self, texts: Sequence[str]) -> list[list[float]]:
        """Encode already-batched texts with the configured batch size."""
        if not texts:
            return []
        vectors = self._model.encode(
            list(texts),
            batch_size=self.spec.batch_size,
            normalize_embeddings=self.spec.normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [[float(value) for value in row] for row in vectors]


# ---------------------------------------------------------------------------
# Vector maths
# ---------------------------------------------------------------------------


def l2_norm(vector: Sequence[float]) -> float:
    return math.sqrt(sum(float(value) * float(value) for value in vector))


def is_normalized(vector: Sequence[float], tolerance: float = 1e-3) -> bool:
    """Check the actual norm instead of trusting the configuration flag."""
    if not vector:
        return False
    return abs(l2_norm(vector) - 1.0) <= tolerance


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    """Cosine similarity, computed directly rather than via a dot product.

    The dot-product shortcut is only valid for unit vectors, so this function
    divides by the norms itself and stays correct even if a caller hands over
    an un-normalised vector.
    """
    if len(left) != len(right):
        raise ValueError(f"dimension mismatch: {len(left)} != {len(right)}")
    if not left:
        raise ValueError("cannot compute similarity of an empty vector")
    dot = sum(float(a) * float(b) for a, b in zip(left, right))
    norm = l2_norm(left) * l2_norm(right)
    if norm == 0.0:
        return 0.0
    return dot / norm


def contains_non_finite(vector: Sequence[float]) -> bool:
    return any(math.isnan(float(value)) or math.isinf(float(value)) for value in vector)


# ---------------------------------------------------------------------------
# Chunk loading
# ---------------------------------------------------------------------------


def load_chunks(chunks_path: Path) -> tuple[list[dict], str]:
    """Read chunks.jsonl in file order and return it with its SHA-256."""
    raw = chunks_path.read_bytes()
    chunks = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    return chunks, hashlib.sha256(raw).hexdigest()


def chunk_order_key(chunk: dict) -> tuple:
    """Deterministic sort used everywhere chunks are iterated.

    Input file order is already deterministic, but sorting by
    (document, page, structural path, index) makes the ordering independent of
    how the file happened to be written, which the determinism test pins down.
    """
    pages = chunk.get("pages") or []
    return (
        str(chunk.get("document_id") or ""),
        int(chunk.get("start_page") or (min(pages) if pages else 0)),
        int(chunk.get("end_page") or (max(pages) if pages else 0)),
        tuple(str(step.get("type")) for step in chunk.get("structural_path") or []),
        str(chunk.get("clause_number") or ""),
        str(chunk.get("section_number") or ""),
        int(chunk.get("chunk_index") or 0),
        str(chunk.get("chunk_id") or ""),
    )


# ---------------------------------------------------------------------------
# Failure accounting
# ---------------------------------------------------------------------------


@dataclass
class BatchFailure:
    batch_index: int
    chunk_ids: list[str]
    reason: str
    error_type: str
    message: str

    def as_dict(self) -> dict:
        return {
            "batch_index": self.batch_index,
            "chunk_ids": self.chunk_ids,
            "reason": self.reason,
            "error_type": self.error_type,
            "error": self.message,
        }


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------


@dataclass
class EmbeddingRun:
    """Result of one ``scraper embed`` invocation."""

    records: list[dict] = field(default_factory=list)
    failures: list[BatchFailure] = field(default_factory=list)
    vectors: list[list[float]] = field(default_factory=list)
    chunk_ids: list[str] = field(default_factory=list)
    generation_seconds: float = 0.0
    batches: int = 0
    batch_sizes: list[int] = field(default_factory=list)
    truncated_chunk_ids: list[str] = field(default_factory=list)


def embed_chunks(
    chunks: Sequence[dict],
    embedder: SentenceTransformerEmbedder,
    spec: EmbeddingSpec,
    on_progress: Callable[[int, int], None] | None = None,
) -> EmbeddingRun:
    """Embed every chunk in deterministic batches.

    A batch that raises is recorded as a failure with its chunk IDs and the
    error; the run continues with the next batch so one bad input cannot
    discard the other 82 chunks. Nothing is ever silently dropped.
    """
    run = EmbeddingRun()
    prepared = [(chunk, build_embedding_text(chunk, spec)) for chunk in chunks]
    run.truncated_chunk_ids = [
        chunk["chunk_id"] for chunk, text in prepared if chunk.get("chunk_id") and embedder.will_truncate(text)
    ]
    batches = list(batched(prepared, spec.batch_size))
    started = time.perf_counter()
    for batch_index, batch in enumerate(batches):
        try:
            vectors = embedder.encode([text for _, text in batch])
            if len(vectors) != len(batch):
                raise ValueError(
                    f"model returned {len(vectors)} vectors for {len(batch)} inputs"
                )
        except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
            run.failures.append(
                BatchFailure(
                    batch_index=batch_index,
                    chunk_ids=[str(chunk.get("chunk_id")) for chunk, _ in batch],
                    reason="batch_encoding_failed",
                    error_type=type(exc).__name__,
                    message=str(exc),
                )
            )
            continue
        for (chunk, _), vector in zip(batch, vectors):
            record = build_record(chunk, vector, embedder, spec)
            run.records.append(record)
            run.vectors.append(vector)
            run.chunk_ids.append(str(chunk.get("chunk_id")))
        run.batches += 1
        run.batch_sizes.append(len(batch))
        if on_progress is not None:
            on_progress(len(run.records), len(prepared))
    run.generation_seconds = time.perf_counter() - started
    return run


def build_record(chunk: dict, vector: Sequence[float], embedder, spec: EmbeddingSpec) -> dict:
    """One embedding record: full provenance plus the vector and its identity."""
    record: dict = {}
    for name in PROVENANCE_FIELDS:
        record[name] = chunk.get(name)
    for name in PASSTHROUGH_FIELDS:
        value = chunk.get(name)
        if value is not None:
            record[name] = value
    record["embedding_model"] = spec.model_name
    record["embedding_model_revision"] = spec.revision or None
    record["embedding_provider"] = spec.provider
    record["embedding_dimension"] = embedder.dimension
    record["normalized"] = spec.normalize
    record["embedding_pooling"] = spec.pooling
    record["embedding"] = [float(value) for value in vector]
    return record


def embed_queries(
    queries: Sequence[str],
    embedder: SentenceTransformerEmbedder,
    spec: EmbeddingSpec,
) -> list[list[float]]:
    """Encode queries with the same model, batch size, and normalisation."""
    if not queries:
        return []
    texts = [f"{spec.query_prefix}{query}" if spec.query_prefix else query for query in queries]
    return embedder.encode(texts)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def validate_embeddings(
    chunks: Sequence[dict],
    records: Sequence[dict],
    failures: Sequence[BatchFailure],
    embedder,
    chunks_sha256: str,
    chunks_unchanged: bool = False,
) -> dict:
    """Run the ten Stage 3.1 acceptance checks and return their results.

    ``chunks_unchanged`` is passed in by the caller, which is the only place
    that can compare the input file's bytes before and after the run.
    """
    expected_dimension = embedder.dimension
    chunk_by_id = {str(chunk.get("chunk_id")): chunk for chunk in chunks}
    record_ids = [str(record.get("chunk_id")) for record in records]
    failed_ids = {chunk_id for failure in failures for chunk_id in failure.chunk_ids}

    wrong_dimension = [
        record.get("chunk_id") for record in records
        if len(record.get("embedding") or []) != expected_dimension
    ]
    non_finite = [
        record.get("chunk_id") for record in records
        if contains_non_finite(record.get("embedding") or [])
    ]
    duplicate_ids = sorted({cid for cid, count in Counter(record_ids).items() if count > 1})
    duplicate_input_ids = sorted(
        {cid for cid, count in Counter(str(chunk.get("chunk_id")) for chunk in chunks).items() if count > 1}
    )
    unmapped = [cid for cid in record_ids if cid not in chunk_by_id]
    unaccounted = [cid for cid in chunk_by_id if cid not in set(record_ids) and cid not in failed_ids]

    metadata_errors: list[dict] = []
    for record in records:
        source = chunk_by_id.get(str(record.get("chunk_id")))
        if source is None:
            continue
        mismatched = [
            field for field in PROVENANCE_FIELDS
            if field != "chunk_id" and record.get(field) != source.get(field)
        ]
        if mismatched:
            metadata_errors.append({"chunk_id": record.get("chunk_id"), "mismatched_fields": mismatched})

    checks = {
        "input_chunk_count_matches": len(chunks) == len(chunk_by_id),
        "successful_embedding_count_matches": len(records) + len(failed_ids) == len(chunks),
        "failure_count_matches": len(failed_ids) == sum(
            len(failure.chunk_ids) for failure in failures
        ),
        "all_dimensions_match_model": not wrong_dimension,
        "no_nan_values": not any(
            math.isnan(float(v)) for record in records for v in record.get("embedding") or []
        ),
        "no_infinite_values": not any(
            math.isinf(float(v)) for record in records for v in record.get("embedding") or []
        ),
        "chunk_ids_unique": not duplicate_ids and not duplicate_input_ids,
        "every_embedding_maps_to_chunk": not unmapped,
        "all_chunks_embedded_or_failed": not unaccounted,
        "source_metadata_preserved": not metadata_errors,
        "chunks_input_unchanged": bool(chunks_unchanged),
    }
    details = {
        "expected_embedding_dimension": expected_dimension,
        "successful_embeddings": len(records),
        "failed_embeddings": len(failed_ids),
        "input_chunks": len(chunks),
        "wrong_dimension_chunk_ids": wrong_dimension,
        "non_finite_chunk_ids": non_finite,
        "duplicate_chunk_ids": duplicate_ids,
        "duplicate_input_chunk_ids": duplicate_input_ids,
        "unmapped_chunk_ids": unmapped,
        "unaccounted_chunk_ids": unaccounted,
        "metadata_errors": metadata_errors,
    }
    checks["passed"] = all(value for key, value in checks.items() if key != "passed")
    return {"checks": checks, "details": details, "chunks_sha256": chunks_sha256}


# ---------------------------------------------------------------------------
# Semantic sanity test
# ---------------------------------------------------------------------------


def semantic_sanity_test(
    queries: Sequence[str],
    records: Sequence[dict],
    embedder: SentenceTransformerEmbedder,
    spec: EmbeddingSpec,
    top_k: int = DEFAULT_SEMANTIC_TEST_TOP_K,
) -> dict:
    """Rank chunks against a handful of fixed queries using the same model.

    This is a sanity probe. It is intentionally not the retrieval API: no
    filters, no reranking, no generation.
    """
    started = time.perf_counter()
    query_vectors = embed_queries(queries, embedder, spec)
    record_vectors = [record["embedding"] for record in records]
    all_normalized = bool(records) and all(
        is_normalized(vector) for vector in record_vectors
    ) and all(is_normalized(vector) for vector in query_vectors)

    results: list[dict] = []
    for query, query_vector in zip(queries, query_vectors):
        scored = [
            (cosine_similarity(query_vector, record["embedding"]), index)
            for index, record in enumerate(records)
        ]
        # Sort by score, then by chunk id, so equal scores rank deterministically.
        scored.sort(key=lambda item: (-item[0], str(records[item[1]].get("chunk_id"))))
        top: list[dict] = []
        for rank, (similarity, index) in enumerate(scored[:top_k], 1):
            record = records[index]
            top.append(
                {
                    "rank": rank,
                    "chunk_id": record.get("chunk_id"),
                    "similarity": round(float(similarity), 6),
                    "standard": ", ".join(record.get("standard_numbers") or []) or None,
                    "clause": record.get("clause_number") or record.get("section_number") or None,
                    "clause_title": record.get("clause_title"),
                    "annex": record.get("annex_identifier"),
                    "page": record.get("start_page"),
                    "text": record.get("text"),
                }
            )
        results.append({"query": query, "results": top})

    return {
        "generated_at": utc_now(),
        "note": (
            "Sanity probe only. Cosine similarity over normalized Stage 3.1 "
            "embeddings. Not a retrieval API, not a ranking model, and not an "
            "evaluation of retrieval quality."
        ),
        "model": spec.model_name,
        "model_revision": spec.revision or None,
        "embedding_dimension": embedder.dimension,
        "normalized": spec.normalize,
        "all_vectors_normalized": all_normalized,
        "top_k": top_k,
        "chunks_searched": len(records),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "queries": results,
    }


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run_embeddings(
    settings: Settings,
    product_id: str | None = None,
    all_documents: bool = False,
    spec: EmbeddingSpec | None = None,
    embedder: SentenceTransformerEmbedder | None = None,
    queries: Sequence[str] | None = None,
    top_k: int = DEFAULT_SEMANTIC_TEST_TOP_K,
    chunks_path: Path | None = None,
    output_dir: Path | None = None,
) -> dict:
    """Embed the existing chunks and write the Stage 3.1 artefacts."""
    started = time.perf_counter()
    spec = spec or resolve_embedding_spec()
    spec.validate()
    chunks_path = chunks_path or (settings.chunks_dir / "chunks.jsonl")
    output_dir = output_dir or settings.embeddings_dir
    if not chunks_path.exists():
        raise FileNotFoundError(
            f"{chunks_path} not found. Run `.venv/bin/python -m scraper chunk` first."
        )

    before_bytes = chunks_path.read_bytes()
    chunks, chunks_sha256 = load_chunks(chunks_path)
    ordered = sorted(chunks, key=chunk_order_key)
    if product_id:
        # Chunks carry no product_id; resolve it from the structured documents.
        allowed = _documents_for_product(settings, product_id)
        ordered = [chunk for chunk in ordered if str(chunk.get("document_id")) in allowed]

    output_dir.mkdir(parents=True, exist_ok=True)
    own_embedder = embedder is None
    if own_embedder:
        embedder = SentenceTransformerEmbedder(spec)
    info = embedder.info()

    run = embed_chunks(ordered, embedder, spec)
    after_bytes = chunks_path.read_bytes()
    chunks_unchanged = before_bytes == after_bytes
    if not chunks_unchanged:
        raise RuntimeError(f"chunks input changed during embedding: {chunks_path}")

    validation = validate_embeddings(
        ordered, run.records, run.failures, embedder, chunks_sha256, chunks_unchanged=chunks_unchanged
    )

    jsonl_path = output_dir / "embeddings.jsonl"
    jsonl_path.write_text(
        "".join(
            json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n" for record in run.records
        ),
        encoding="utf-8",
    )

    document_ids = sorted({str(record.get("document_id")) for record in run.records})
    standards: Counter = Counter()
    for record in run.records:
        for standard in record.get("standard_numbers") or []:
            standards[standard] += 1

    total_seconds = time.perf_counter() - started
    successful = len(run.records)
    report = {
        "generated_at": utc_now(),
        "stage": "3.1",
        "selection": "all_chunks" if all_documents else product_id or "stage_2_5_chunks",
        "documents": len(document_ids),
        "input_chunks": len(ordered),
        "successful_embeddings": successful,
        "failed_embeddings": sum(len(failure.chunk_ids) for failure in run.failures),
        "model": spec.model_name,
        "embedding_dimension": info.dimension,
        "normalized": spec.normalize,
        "batch_size": spec.batch_size,
        "model_detail": info.as_dict(),
        "batch_detail": {
            "batches_encoded": run.batches,
            "batch_sizes_observed": run.batch_sizes,
            "final_partial_batch": (
                run.batch_sizes[-1] if run.batch_sizes and run.batch_sizes[-1] < spec.batch_size else None
            ),
            "batches_failed": len(run.failures),
        },
        "timing": {
            "model_load_seconds": round(info.load_seconds, 3),
            "embedding_generation_seconds": round(run.generation_seconds, 3),
            "average_seconds_per_chunk": round(run.generation_seconds / successful, 4) if successful else 0.0,
            "total_runtime_seconds": round(total_seconds, 3),
        },
        "chunks_per_document": dict(sorted(Counter(
            str(record.get("document_id")) for record in run.records
        ).items())),
        "chunks_per_standard": dict(sorted(standards.items())),
        "truncation": {
            "model_max_seq_length": info.max_seq_length,
            "chunks_truncated": len(run.truncated_chunk_ids),
            "truncated_chunk_ids": run.truncated_chunk_ids,
            "note": (
                "Chunks longer than the model's context window are encoded from "
                "their leading tokens. The stored text remains complete and "
                "unchanged; only the embedded string is windowed."
            ),
        },
        "failures": [failure.as_dict() for failure in run.failures],
        "validation": {"checks": validation["checks"], "details": validation["details"]},
        "input_provenance": {
            "chunks_file": str(chunks_path),
            "chunks_sha256": chunks_sha256,
            "chunks_unchanged": chunks_unchanged,
        },
        "outputs": {
            "embeddings": str(jsonl_path),
            "report": str(output_dir / "embedding_report.json"),
            "semantic_test": str(output_dir / "semantic_test.json"),
        },
    }
    atomic_json(output_dir / "embedding_report.json", report)

    chosen_queries = list(queries if queries is not None else SEMANTIC_TEST_QUERIES)
    semantic = semantic_sanity_test(chosen_queries, run.records, embedder, spec, top_k=top_k)
    atomic_json(output_dir / "semantic_test.json", semantic)
    return report


def _documents_for_product(settings: Settings, product_id: str) -> set[str]:
    structured_dir = settings.data_dir / "processed" / "structured"
    found: set[str] = set()
    for path in sorted(structured_dir.glob("*.json")):
        if path.name == "structure_report.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if document.get("product_id") == product_id:
            found.add(str(document.get("document_id")))
    return found
