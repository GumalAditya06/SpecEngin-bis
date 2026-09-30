"""Stage 3.3 hybrid retrieval: BM25 + semantic RRF + cross-encoder reranking.

This module is retrieval-only.  It reads the immutable Stage 2.5 chunk corpus
and the existing Stage 3.2 vector store; it never rewrites either one.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .retrieval import Result, RetrievalError, build_filter_mask
from .vectorstore import FILTERABLE_FIELDS

DEFAULT_CANDIDATE_K = 20
DEFAULT_RERANK_K = 40
DEFAULT_FINAL_K = 5
DEFAULT_RRF_K = 60
DEFAULT_BM25_K1 = 1.5
DEFAULT_BM25_B = 0.75

RERANKER_MODEL = "cross-encoder/ms-marco-MiniLM-L6-v2"
RERANKER_REVISION = "233902d25c440f23af6f7d6e94d2946bac0bee0a"
RERANKER_PARAMETERS = 22_713_601

# Keep standards and dotted clauses intact (``4250:2025``, ``1.3.1``), while
# still emitting ordinary words. This is deliberately not a language stemmer:
# exact BIS identifiers and terminology are the lexical layer's main job.
TOKEN_RE = re.compile(r"[a-z0-9]+(?:[.:/-][a-z0-9]+)*", re.IGNORECASE)

INDEXED_FIELDS: tuple[str, ...] = (
    "context_prefix",
    "text",
    "standard_numbers",
    "clause_number",
    "clause_title",
)


def lexical_tokens(text: str) -> list[str]:
    """Return deterministic lowercase BM25 tokens, preserving identifiers."""
    return TOKEN_RE.findall(str(text).lower())


def build_searchable_text(record: dict) -> str:
    """Build the documented BM25 representation without guessing metadata.

    Exact duplicate components are included only once. The authoritative
    ``text`` value itself is never normalized or rewritten.
    """
    values: list[str] = []
    raw_values = [
        record.get("context_prefix"),
        record.get("text"),
        *(record.get("standard_numbers") or []),
        record.get("clause_number"),
        record.get("clause_title"),
    ]
    seen: set[str] = set()
    for value in raw_values:
        rendered = str(value or "").strip()
        if rendered and rendered not in seen:
            seen.add(rendered)
            values.append(rendered)
    return "\n".join(values)


def load_chunks(path: Path) -> list[dict]:
    if not path.exists():
        raise RetrievalError(f"chunk corpus not found: {path}")
    records: list[dict] = []
    ids: set[str] = set()
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            record = json.loads(line)
            chunk_id = str(record.get("chunk_id") or "")
            if not chunk_id:
                raise RetrievalError(f"missing chunk_id at {path}:{line_number}")
            if chunk_id in ids:
                raise RetrievalError(f"duplicate chunk_id in corpus: {chunk_id}")
            ids.add(chunk_id)
            records.append(record)
    return records


@dataclass(frozen=True)
class BM25Result:
    rank: int
    chunk_id: str
    bm25_score: float
    metadata: dict
    text: str

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "chunk_id": self.chunk_id,
            "bm25_score": round(self.bm25_score, 6),
            "metadata": dict(self.metadata),
            "text": self.text,
        }


class BM25Index:
    """Small deterministic Okapi BM25 index over chunk records."""

    def __init__(
        self,
        records: Sequence[dict],
        *,
        k1: float = DEFAULT_BM25_K1,
        b: float = DEFAULT_BM25_B,
        metadata_records: Sequence[dict] | None = None,
    ):
        if k1 <= 0 or not 0 <= b <= 1:
            raise ValueError("BM25 requires k1 > 0 and 0 <= b <= 1")
        supplemental = {
            str(record.get("chunk_id")): record for record in (metadata_records or [])
        }
        self.records = []
        for source in records:
            record = dict(source)
            # Stage 3.2 derives ``product`` from the manifest. Preserve that
            # recorded value for filtering, but never use it as indexed text.
            extra = supplemental.get(str(record.get("chunk_id")), {})
            for field in FILTERABLE_FIELDS:
                stored = "standard_numbers" if field == "standard_number" else field
                if record.get(stored) in (None, "", []) and extra.get(stored) not in (None, "", []):
                    record[stored] = extra[stored]
            self.records.append(record)
        self.k1 = float(k1)
        self.b = float(b)
        self.documents = [lexical_tokens(build_searchable_text(record)) for record in self.records]
        self.lengths = [len(tokens) for tokens in self.documents]
        self.average_length = sum(self.lengths) / len(self.lengths) if self.lengths else 0.0
        self.term_frequencies = [Counter(tokens) for tokens in self.documents]
        document_frequency: Counter[str] = Counter()
        for tokens in self.documents:
            document_frequency.update(set(tokens))
        size = len(self.documents)
        self.idf = {
            term: math.log(1.0 + (size - frequency + 0.5) / (frequency + 0.5))
            for term, frequency in document_frequency.items()
        }

    @classmethod
    def from_jsonl(cls, path: Path, **kwargs) -> "BM25Index":
        return cls(load_chunks(path), **kwargs)

    def search(
        self, query: str, top_k: int = DEFAULT_CANDIDATE_K, filters: dict | None = None
    ) -> list[BM25Result]:
        if not isinstance(query, str) or not query.strip():
            raise RetrievalError("query must be a non-empty string")
        if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
            raise RetrievalError("top_k must be a positive integer")
        mask, unknown = build_filter_mask(self.records, filters)
        if unknown:
            raise RetrievalError(
                f"unsupported filter field(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(FILTERABLE_FIELDS)}"
            )
        query_terms = Counter(lexical_tokens(query))
        scores: list[tuple[float, int]] = []
        for index, frequencies in enumerate(self.term_frequencies):
            if mask is not None and not bool(mask[index]):
                continue
            score = 0.0
            length = self.lengths[index]
            normalizer = self.k1 * (
                1.0 - self.b + self.b * length / (self.average_length or 1.0)
            )
            for term, query_frequency in query_terms.items():
                frequency = frequencies.get(term, 0)
                if frequency:
                    score += (
                        self.idf[term]
                        * frequency
                        * (self.k1 + 1.0)
                        / (frequency + normalizer)
                        * query_frequency
                    )
            if score > 0:
                scores.append((score, index))
        scores.sort(key=lambda item: (-item[0], item[1]))
        return [
            BM25Result(
                rank=rank,
                chunk_id=str(self.records[index]["chunk_id"]),
                bm25_score=score,
                metadata=dict(self.records[index]),
                text=str(self.records[index].get("text") or ""),
            )
            for rank, (score, index) in enumerate(scores[:top_k], start=1)
        ]


def bm25_search(
    index: BM25Index, query: str, top_k: int = DEFAULT_CANDIDATE_K,
    filters: dict | None = None,
) -> list[BM25Result]:
    """Functional entry point requested by the Stage 3.3 contract."""
    return index.search(query, top_k=top_k, filters=filters)


@dataclass
class FusedCandidate:
    chunk_id: str
    record: dict
    fusion_score: float = 0.0
    semantic_rank: int | None = None
    bm25_rank: int | None = None
    semantic_score: float | None = None
    bm25_score: float | None = None


def reciprocal_rank_fusion(
    semantic_results: Sequence[Result],
    bm25_results: Sequence[BM25Result],
    *,
    rrf_k: int = DEFAULT_RRF_K,
) -> list[FusedCandidate]:
    """Fuse ranked lists by RRF and remove duplicate chunk IDs."""
    if not isinstance(rrf_k, int) or isinstance(rrf_k, bool) or rrf_k < 1:
        raise RetrievalError("rrf_k must be a positive integer")
    candidates: dict[str, FusedCandidate] = {}
    for result in semantic_results:
        record = result.to_dict()
        candidate = candidates.setdefault(
            result.chunk_id, FusedCandidate(result.chunk_id, record)
        )
        if candidate.semantic_rank is None:
            candidate.semantic_rank = result.rank
            candidate.semantic_score = result.similarity_score
            candidate.fusion_score += 1.0 / (rrf_k + result.rank)
    for result in bm25_results:
        candidate = candidates.setdefault(
            result.chunk_id, FusedCandidate(result.chunk_id, dict(result.metadata))
        )
        if candidate.bm25_rank is None:
            candidate.bm25_rank = result.rank
            candidate.bm25_score = result.bm25_score
            candidate.fusion_score += 1.0 / (rrf_k + result.rank)
    return sorted(
        candidates.values(),
        key=lambda item: (-item.fusion_score, item.chunk_id),
    )


def build_reranker_text(record: dict) -> str:
    """Compose structural context plus unmodified authoritative chunk text."""
    prefix = str(record.get("context_prefix") or "").strip()
    text = str(record.get("text") or "")
    return f"{prefix}\n\n{text}" if prefix else text


class CrossEncoderReranker:
    """Pinned local sentence-transformers cross-encoder wrapper."""

    def __init__(
        self,
        model_name: str = RERANKER_MODEL,
        revision: str = RERANKER_REVISION,
        *,
        device: str = "cpu",
        batch_size: int = 16,
    ):
        from sentence_transformers import CrossEncoder

        self.model_name = model_name
        self.revision = revision
        self.device = device
        self.batch_size = batch_size
        self.model = CrossEncoder(model_name, revision=revision, device=device, max_length=512)

    def score(self, query: str, documents: Sequence[str]) -> list[float]:
        if not documents:
            return []
        scores = self.model.predict(
            [(query, document) for document in documents],
            batch_size=self.batch_size,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return [float(score) for score in scores.reshape(-1)]


@dataclass(frozen=True)
class HybridResult:
    rank: int
    chunk_id: str
    reranker_score: float
    fusion_score: float
    semantic_rank: int | None
    bm25_rank: int | None
    record: dict

    def to_dict(self) -> dict:
        record = self.record
        return {
            "rank": self.rank,
            "chunk_id": self.chunk_id,
            "reranker_score": round(self.reranker_score, 6),
            "fusion_score": round(self.fusion_score, 8),
            # Zero is the public sentinel for "not returned by this source";
            # internal Optional values remain useful while fusing.
            "semantic_rank": self.semantic_rank or 0,
            "bm25_rank": self.bm25_rank or 0,
            "standard_numbers": list(record.get("standard_numbers") or []),
            "clause_number": record.get("clause_number"),
            "clause_title": record.get("clause_title"),
            "pages": list(record.get("pages") or []),
            "text": str(record.get("text") or ""),
            "source_url": record.get("source_url"),
            "document_id": record.get("document_id"),
            "document_type": record.get("document_type"),
            "product": record.get("product"),
            "annex_identifier": record.get("annex_identifier"),
            "context_prefix": record.get("context_prefix"),
        }


class HybridRetriever:
    def __init__(self, semantic_retriever, bm25_index: BM25Index, reranker):
        self.semantic = semantic_retriever
        self.bm25 = bm25_index
        self.reranker = reranker
        semantic_ids = set(getattr(semantic_retriever.store, "chunk_ids", []))
        bm25_ids = {str(record.get("chunk_id")) for record in bm25_index.records}
        if semantic_ids != bm25_ids:
            raise RetrievalError(
                "BM25 corpus is incompatible with the existing vector store "
                f"({len(bm25_ids)} lexical IDs vs {len(semantic_ids)} vector IDs)"
            )

    def search(
        self,
        query: str,
        *,
        candidate_k: int = DEFAULT_CANDIDATE_K,
        rerank_k: int = DEFAULT_RERANK_K,
        final_k: int = DEFAULT_FINAL_K,
        rrf_k: int = DEFAULT_RRF_K,
        filters: dict | None = None,
    ) -> tuple[list[HybridResult], dict]:
        for name, value in (("candidate_k", candidate_k), ("rerank_k", rerank_k), ("final_k", final_k)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise RetrievalError(f"{name} must be a positive integer")
        total_start = time.perf_counter()
        started = time.perf_counter()
        semantic = self.semantic.search(query, top_k=candidate_k, filters=filters)
        semantic_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        lexical = self.bm25.search(query, top_k=candidate_k, filters=filters)
        bm25_ms = (time.perf_counter() - started) * 1000
        started = time.perf_counter()
        fused = reciprocal_rank_fusion(semantic, lexical, rrf_k=rrf_k)
        fusion_ms = (time.perf_counter() - started) * 1000
        reranked_pool = fused[:rerank_k]
        started = time.perf_counter()
        scores = self.reranker.score(
            query, [build_reranker_text(candidate.record) for candidate in reranked_pool]
        )
        reranking_ms = (time.perf_counter() - started) * 1000
        ranked = sorted(
            zip(reranked_pool, scores),
            key=lambda pair: (-pair[1], -pair[0].fusion_score, pair[0].chunk_id),
        )
        results = [
            HybridResult(
                rank=rank,
                chunk_id=candidate.chunk_id,
                reranker_score=score,
                fusion_score=candidate.fusion_score,
                semantic_rank=candidate.semantic_rank,
                bm25_rank=candidate.bm25_rank,
                record=candidate.record,
            )
            for rank, (candidate, score) in enumerate(ranked[:final_k], start=1)
        ]
        timings = {
            "bm25_ms": round(bm25_ms, 3),
            "semantic_ms": round(semantic_ms, 3),
            "fusion_ms": round(fusion_ms, 3),
            "reranking_ms": round(reranking_ms, 3),
            "total_ms": round((time.perf_counter() - total_start) * 1000, 3),
            "semantic_candidates": len(semantic),
            "bm25_candidates": len(lexical),
            "unique_fused_candidates": len(fused),
            "reranked_candidates": len(reranked_pool),
        }
        return results, timings
