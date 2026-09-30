"""Stable Stage 3.4 service boundary for retrieval-only evidence.

The service owns lifecycle, validation, exact-identifier restrictions, and the
consumer-facing evidence contract. Ranking remains exclusively in
``hybrid_retrieval.py``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence

from .config import Settings, resolve_embedding_spec
from .embeddings import SentenceTransformerEmbedder
from .hybrid_retrieval import (
    DEFAULT_CANDIDATE_K,
    DEFAULT_FINAL_K,
    DEFAULT_RERANK_K,
    DEFAULT_RRF_K,
    BM25Index,
    CrossEncoderReranker,
    HybridResult,
    HybridRetriever,
)
from .retrieval import RetrievalError, Retriever
from .vectorstore import FILTERABLE_FIELDS, load_index

PUBLIC_FILTERS = frozenset(FILTERABLE_FIELDS)
EXACT_STANDARD_RE = re.compile(r"(?<![A-Z0-9])IS\s*[-/]?\s*(\d+)(?::(\d{4}))?\b", re.IGNORECASE)


@dataclass(frozen=True)
class EvidenceSource:
    document_id: str | None
    source_id: str | None
    title: str | None
    version: str | int | None
    page: int | None
    url: str | None


@dataclass(frozen=True)
class EvidenceRetrieval:
    methods: tuple[str, ...]
    semantic_rank: int | None
    bm25_rank: int | None
    rrf_score: float
    reranker_score: float


@dataclass(frozen=True)
class EvidenceResult:
    rank: int
    chunk_id: str
    standard_number: str | None
    clause_number: str | None
    clause_title: str | None
    text: str
    context_prefix: str | None
    source: EvidenceSource
    retrieval: EvidenceRetrieval

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RetrievalResponse:
    query: str
    results: tuple[EvidenceResult, ...]
    timings: Mapping[str, float | int]

    def to_dict(self, *, include_timings: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "query": self.query,
            "results": [result.to_dict() for result in self.results],
        }
        if include_timings:
            payload["timings"] = dict(self.timings)
        return payload


def _clean_filters(filters: Mapping[str, Any] | None) -> dict[str, str]:
    if filters is None:
        return {}
    if not isinstance(filters, Mapping):
        raise RetrievalError("filters must be an object")
    unknown = sorted(set(filters) - PUBLIC_FILTERS)
    if unknown:
        raise RetrievalError(
            f"unsupported filter field(s): {', '.join(unknown)}. "
            f"Available: {', '.join(sorted(PUBLIC_FILTERS))}"
        )
    cleaned: dict[str, str] = {}
    for field, value in filters.items():
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            raise RetrievalError(f"filter '{field}' must be a string or integer")
        rendered = str(value).strip()
        if not rendered:
            raise RetrievalError(f"filter '{field}' must not be empty")
        cleaned[field] = rendered
    return cleaned


def _identifier(query: str) -> tuple[str, str | None] | None:
    match = EXACT_STANDARD_RE.search(query)
    return (match.group(1), match.group(2)) if match else None


def _standard_values(records: Sequence[dict]) -> tuple[str, ...]:
    return tuple(sorted({str(value) for record in records for value in (record.get("standard_numbers") or [])}))


def _resolve_identifier(query: str, standard_values: Sequence[str]) -> str | None:
    identifier = _identifier(query)
    if identifier is None:
        return None
    number, year = identifier
    suffix = rf":{re.escape(year)}" if year else r"(?::\d{4})?"
    pattern = re.compile(rf"^IS\s+{re.escape(number)}{suffix}$", re.IGNORECASE)
    matches = [value for value in standard_values if pattern.fullmatch(value)]
    # This corpus currently has one authoritative edition per requested short
    # identifier. Refuse ambiguity instead of guessing if that changes later.
    if len(matches) > 1:
        raise RetrievalError(
            f"standard identifier 'IS {number}' matches multiple editions; specify the year"
        )
    return matches[0] if matches else ""


def _first_page(record: Mapping[str, Any]) -> int | None:
    pages = record.get("pages") or []
    if pages:
        try:
            return int(pages[0])
        except (TypeError, ValueError):
            return None
    page = record.get("start_page")
    try:
        return int(page) if page is not None else None
    except (TypeError, ValueError):
        return None


def evidence_from_hybrid(result: HybridResult) -> EvidenceResult:
    record = result.record
    standards = record.get("standard_numbers") or []
    methods = tuple(
        method
        for method, rank in (("semantic", result.semantic_rank), ("bm25", result.bm25_rank))
        if rank is not None
    )
    return EvidenceResult(
        rank=result.rank,
        chunk_id=result.chunk_id,
        standard_number=str(standards[0]) if standards else None,
        clause_number=record.get("clause_number"),
        clause_title=record.get("clause_title"),
        text=str(record.get("text") or ""),
        context_prefix=record.get("context_prefix"),
        source=EvidenceSource(
            document_id=record.get("document_id"),
            source_id=record.get("source_id"),
            title=record.get("document_title"),
            version=record.get("document_version"),
            page=_first_page(record),
            url=record.get("source_url"),
        ),
        retrieval=EvidenceRetrieval(
            methods=methods,
            semantic_rank=result.semantic_rank,
            bm25_rank=result.bm25_rank,
            rrf_score=round(result.fusion_score, 8),
            reranker_score=round(result.reranker_score, 6),
        ),
    )


class RetrievalService:
    """Query-to-evidence boundary with the frozen Stage 3.3 configuration."""

    def __init__(self, retriever: HybridRetriever):
        self._retriever = retriever
        self._standard_values = _standard_values(retriever.bm25.records)

    @classmethod
    def from_settings(cls, settings: Settings) -> "RetrievalService":
        embeddings_path = settings.embeddings_v2_dir / "embeddings.jsonl"
        store = load_index(settings.vector_store_dir, embeddings_path)
        spec = resolve_embedding_spec()
        semantic = Retriever(
            store,
            embedder=SentenceTransformerEmbedder(spec),
            spec=spec,
        )
        chunks_path = settings.processed_dir / "chunks_v2" / "chunks.jsonl"
        lexical = BM25Index.from_jsonl(chunks_path, metadata_records=store.metadata)
        return cls(HybridRetriever(semantic, lexical, CrossEncoderReranker()))

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "RetrievalService":
        return cls.from_settings(Settings(data_dir=Path(data_dir)))

    def retrieve(
        self,
        query: str,
        top_k: int = DEFAULT_FINAL_K,
        filters: Mapping[str, Any] | None = None,
    ) -> RetrievalResponse:
        if not isinstance(query, str) or not query.strip():
            raise RetrievalError("query must be a non-empty string")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or not 1 <= top_k <= DEFAULT_FINAL_K:
            raise RetrievalError(f"top_k must be an integer between 1 and {DEFAULT_FINAL_K}")
        cleaned = _clean_filters(filters)
        exact_standard = _resolve_identifier(query, self._standard_values)
        if exact_standard == "":
            return RetrievalResponse(query=query.strip(), results=(), timings={})
        if exact_standard:
            supplied = cleaned.get("standard_number")
            if supplied and supplied.casefold() != exact_standard.casefold():
                return RetrievalResponse(query=query.strip(), results=(), timings={})
            cleaned["standard_number"] = exact_standard

        results, timings = self._retriever.search(
            query.strip(),
            candidate_k=DEFAULT_CANDIDATE_K,
            rerank_k=DEFAULT_RERANK_K,
            final_k=top_k,
            rrf_k=DEFAULT_RRF_K,
            filters=cleaned or None,
        )
        return RetrievalResponse(
            query=query.strip(),
            results=tuple(evidence_from_hybrid(result) for result in results),
            timings=timings,
        )


def retrieve(
    service: RetrievalService,
    query: str,
    top_k: int = DEFAULT_FINAL_K,
    filters: Mapping[str, Any] | None = None,
) -> RetrievalResponse:
    """Small functional interface for callers that manage service lifecycle."""
    return service.retrieve(query, top_k=top_k, filters=filters)
