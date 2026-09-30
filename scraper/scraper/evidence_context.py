"""Stage 4.1 deterministic evidence packaging for a future grounded answerer.

This module performs no retrieval and calls no language model. It validates and
formats the ranked evidence returned by :class:`RetrievalService` without
changing ranking, authoritative text, or provenance.
"""

from __future__ import annotations

from typing import Literal, Sequence

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .retrieval_service import EvidenceResult

DEFAULT_MAX_CONTEXT_CHARS = 20_000
UNAVAILABLE = "Unavailable"


class EvidenceContextError(ValueError):
    """Raised when retrieval evidence cannot form a trustworthy package."""


class EvidenceItem(BaseModel):
    """One immutable, provenance-preserving item for future grounding."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(pattern=r"^E[1-9]\d*$")
    chunk_id: str = Field(min_length=1)
    rank: int = Field(ge=1)
    standard: str | None
    clause_number: str | None
    clause_title: str | None
    authoritative_text: str = Field(min_length=1)
    context_prefix: str | None
    document_id: str | None
    source_id: str | None
    title: str | None
    version: str | int | None
    page: int | None
    url: str | None
    retrieval_methods: tuple[Literal["semantic", "bm25"], ...]
    semantic_rank: int | None = Field(default=None, ge=1)
    bm25_rank: int | None = Field(default=None, ge=1)
    rrf_score: float
    reranker_score: float


class OmittedEvidence(BaseModel):
    """Explicit record of a whole item excluded by the context budget."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str = Field(pattern=r"^E[1-9]\d*$")
    chunk_id: str
    rank: int = Field(ge=1)
    reason: Literal["context_budget_exceeded"]
    required_chars: int = Field(ge=1)


class EvidencePackage(BaseModel):
    """Validated context plus its exact structured evidence."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str = Field(min_length=1)
    evidence: list[EvidenceItem]
    evidence_count: int = Field(ge=0)
    has_evidence: bool
    context_text: str
    max_context_chars: int = Field(ge=1)
    omitted_evidence: list[OmittedEvidence] = Field(default_factory=list)
    omitted_count: int = Field(ge=0)

    @model_validator(mode="after")
    def validate_consistency(self) -> "EvidencePackage":
        if self.evidence_count != len(self.evidence):
            raise ValueError("evidence_count does not match evidence")
        if self.omitted_count != len(self.omitted_evidence):
            raise ValueError("omitted_count does not match omitted_evidence")
        if self.has_evidence != bool(self.evidence):
            raise ValueError("has_evidence does not match evidence")
        if len(self.context_text) > self.max_context_chars:
            raise ValueError("context_text exceeds max_context_chars")
        if not self.evidence and self.context_text:
            raise ValueError("context_text must be empty when evidence is empty")

        ids = [item.evidence_id for item in self.evidence]
        if len(ids) != len(set(ids)):
            raise ValueError("evidence IDs must be unique")
        chunks = [item.chunk_id for item in self.evidence]
        if len(chunks) != len(set(chunks)):
            raise ValueError("chunk IDs must be unique")
        if any(item.evidence_id != f"E{item.rank}" for item in self.evidence):
            raise ValueError("evidence IDs must correspond to retrieval ranks")
        if [item.rank for item in self.evidence] != sorted(item.rank for item in self.evidence):
            raise ValueError("evidence must remain in retrieval rank order")

        cursor = -1
        for evidence_id in ids:
            position = self.context_text.find(f"[{evidence_id}]", cursor + 1)
            if position < 0:
                raise ValueError(f"context is missing [{evidence_id}]")
            if position <= cursor:
                raise ValueError("context evidence order does not match evidence")
            cursor = position
        return self


def _display(value: object | None) -> str:
    return UNAVAILABLE if value is None else str(value)


def _format_item(item: EvidenceItem) -> str:
    methods = ", ".join(item.retrieval_methods) or UNAVAILABLE
    return "\n".join(
        (
            f"[{item.evidence_id}]",
            f"Standard: {_display(item.standard)}",
            f"Clause: {_display(item.clause_number)}",
            f"Clause Title: {_display(item.clause_title)}",
            f"Document: {_display(item.title)}",
            f"Document ID: {_display(item.document_id)}",
            f"Source ID: {_display(item.source_id)}",
            f"Version: {_display(item.version)}",
            f"Page: {_display(item.page)}",
            f"URL: {_display(item.url)}",
            f"Retrieval Methods: {methods}",
            "",
            "Context:",
            _display(item.context_prefix),
            "",
            "Authoritative Text:",
            item.authoritative_text,
        )
    )


def _to_item(result: EvidenceResult) -> EvidenceItem:
    if not isinstance(result.chunk_id, str) or not result.chunk_id.strip():
        raise EvidenceContextError("evidence chunk_id must be a non-empty string")
    if not isinstance(result.rank, int) or isinstance(result.rank, bool) or result.rank < 1:
        raise EvidenceContextError("evidence rank must be a positive integer")
    if not isinstance(result.text, str) or not result.text.strip():
        raise EvidenceContextError(
            f"authoritative_text is required for chunk {result.chunk_id}"
        )
    try:
        return EvidenceItem(
            evidence_id=f"E{result.rank}",
            chunk_id=result.chunk_id,
            rank=result.rank,
            standard=result.standard_number,
            clause_number=result.clause_number,
            clause_title=result.clause_title,
            authoritative_text=result.text,
            context_prefix=result.context_prefix,
            document_id=result.source.document_id,
            source_id=result.source.source_id,
            title=result.source.title,
            version=result.source.version,
            page=result.source.page,
            url=result.source.url,
            retrieval_methods=result.retrieval.methods,
            semantic_rank=result.retrieval.semantic_rank,
            bm25_rank=result.retrieval.bm25_rank,
            rrf_score=result.retrieval.rrf_score,
            reranker_score=result.retrieval.reranker_score,
        )
    except (TypeError, ValueError) as exc:
        raise EvidenceContextError(
            f"invalid retrieval evidence for chunk {result.chunk_id}: {exc}"
        ) from exc


class EvidenceContextBuilder:
    """Convert already-ranked retrieval results into deterministic context.

    The budget retains the highest-ranked prefix of whole evidence blocks. At
    the first block that does not fit, that block and every lower-ranked block
    are recorded as omitted. Authoritative text is never sliced or rewritten.
    """

    def __init__(self, max_context_chars: int = DEFAULT_MAX_CONTEXT_CHARS):
        if (
            not isinstance(max_context_chars, int)
            or isinstance(max_context_chars, bool)
            or max_context_chars < 1
        ):
            raise EvidenceContextError("max_context_chars must be a positive integer")
        self.max_context_chars = max_context_chars

    def build(
        self,
        query: str,
        results: Sequence[EvidenceResult],
    ) -> EvidencePackage:
        if not isinstance(query, str) or not query.strip():
            raise EvidenceContextError("query must be a non-empty string")
        if isinstance(results, (str, bytes)) or not isinstance(results, Sequence):
            raise EvidenceContextError("results must be a sequence of EvidenceResult values")
        if any(not isinstance(result, EvidenceResult) for result in results):
            raise EvidenceContextError("results must contain only EvidenceResult values")

        items = [_to_item(result) for result in results]
        ranks = [item.rank for item in items]
        expected_ranks = list(range(1, len(items) + 1))
        if ranks != expected_ranks:
            raise EvidenceContextError(
                "retrieval results must remain in contiguous rank order starting at 1"
            )
        chunk_ids = [item.chunk_id for item in items]
        if len(chunk_ids) != len(set(chunk_ids)):
            raise EvidenceContextError("duplicate chunk_id in retrieval results")

        included: list[EvidenceItem] = []
        blocks: list[str] = []
        omitted: list[OmittedEvidence] = []
        budget_exhausted = False
        context_chars = 0
        for item in items:
            block = _format_item(item)
            separator_chars = 2 if blocks else 0
            if (
                budget_exhausted
                or context_chars + separator_chars + len(block)
                > self.max_context_chars
            ):
                budget_exhausted = True
                omitted.append(
                    OmittedEvidence(
                        evidence_id=item.evidence_id,
                        chunk_id=item.chunk_id,
                        rank=item.rank,
                        reason="context_budget_exceeded",
                        required_chars=len(block),
                    )
                )
                continue
            included.append(item)
            blocks.append(block)
            context_chars += separator_chars + len(block)

        context_text = "\n\n".join(blocks)
        package = EvidencePackage(
            query=query,
            evidence=included,
            evidence_count=len(included),
            has_evidence=bool(included),
            context_text=context_text,
            max_context_chars=self.max_context_chars,
            omitted_evidence=omitted,
            omitted_count=len(omitted),
        )

        # The formatter is deterministic and every included structured item
        # must be represented by the exact block used for budget accounting.
        if package.context_text != "\n\n".join(_format_item(item) for item in package.evidence):
            raise EvidenceContextError("context validation failed")
        return package
