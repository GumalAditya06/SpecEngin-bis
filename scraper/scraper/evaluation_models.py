"""Typed, versioned contracts for Stage 4.4 RAG evaluation artefacts."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

DATASET_VERSION = "1.0.0"

GroundTruthStatus = Literal["VERIFIED", "UNVERIFIED"]
GroundednessReview = Literal["PASS", "FAIL", "UNCERTAIN"]
FailureCategory = Literal[
    "RETRIEVAL_MISS",
    "WRONG_STANDARD",
    "WRONG_CLAUSE",
    "WRONG_DOCUMENT",
    "WRONG_TABLE",
    "WRONG_ANNEX",
    "RERANKING_ERROR",
    "INSUFFICIENT_EVIDENCE_HANDLING",
    "CITATION_ERROR",
    "GROUNDEDNESS_ERROR",
    "GENERATION_ERROR",
    "UNKNOWN",
]

QUESTION_CATEGORIES = frozenset(
    {
        "exact_standard_lookup",
        "definition",
        "requirement",
        "testing",
        "sampling",
        "licensing_certification",
        "clause_specific",
        "table",
        "annex",
        "cross_document",
        "negative_insufficient_evidence",
        "ambiguous",
    }
)


class EvaluationQuestion(BaseModel):
    """One benchmark question; ``None`` means unknown, while ``[]`` means known empty."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    dataset_version: str = DATASET_VERSION
    question_id: str = Field(pattern=r"^q[0-9]{3}$")
    question: str = Field(min_length=1)
    question_type: str
    difficulty: Literal["easy", "medium", "hard"]
    ground_truth_status: GroundTruthStatus
    expected_documents: list[str] | None = None
    expected_standard_numbers: list[str] | None = None
    expected_clause_numbers: list[str] | None = None
    expected_chunk_ids: list[str] | None = None
    expected_pages: list[int] | None = None
    expected_evidence: list[str] | None = None
    expected_evidence_state: Literal[
        "VERIFIED_EVIDENCE", "PARTIAL_EVIDENCE", "NO_VERIFIED_EVIDENCE"
    ] | None = None
    expected_chunks_exhaustive: bool = False
    filters: dict[str, str] | None = None
    notes: str | None = None

    @model_validator(mode="after")
    def validate_ground_truth(self) -> "EvaluationQuestion":
        if self.question_type not in QUESTION_CATEGORIES:
            raise ValueError(f"unsupported question_type: {self.question_type}")
        for name in (
            "expected_documents",
            "expected_standard_numbers",
            "expected_clause_numbers",
            "expected_chunk_ids",
            "expected_pages",
            "expected_evidence",
        ):
            values = getattr(self, name)
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"{name} must not contain duplicates")
        if self.ground_truth_status == "UNVERIFIED":
            populated = any(
                getattr(self, name) not in (None, [])
                for name in (
                    "expected_documents",
                    "expected_standard_numbers",
                    "expected_clause_numbers",
                    "expected_chunk_ids",
                    "expected_pages",
                    "expected_evidence",
                )
            )
            if populated or self.expected_evidence_state is not None:
                raise ValueError("unverified questions must not claim expected evidence")
        if self.ground_truth_status == "VERIFIED":
            has_positive_truth = any(
                getattr(self, name) not in (None, [])
                for name in (
                    "expected_documents",
                    "expected_standard_numbers",
                    "expected_clause_numbers",
                    "expected_chunk_ids",
                    "expected_pages",
                    "expected_evidence",
                )
            )
            no_evidence = self.expected_evidence_state == "NO_VERIFIED_EVIDENCE"
            if not has_positive_truth and not no_evidence:
                raise ValueError("verified questions need evidence ground truth or a no-evidence state")
        return self


class LayerResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    rank: int = Field(ge=1)
    chunk_id: str
    document_id: str | None
    standard_numbers: list[str]
    clause_number: str | None
    annex_identifier: str | None
    table_identifier: str | None
    pages: list[int]
    score: float | None = None


class LayerEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    latency_ms: float
    results: list[LayerResult]


class RetrievalCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    question: str
    question_type: str
    ground_truth_status: GroundTruthStatus
    filters: dict[str, str] | None
    layers: dict[str, LayerEvaluation]
    metrics: dict[str, float | bool | int | None]
    exact_identifier: dict[str, Any] | None = None
    failure_categories: list[FailureCategory] = Field(default_factory=list)


class CitationEvaluation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    citation_exists_when_required: bool
    ids_correspond_to_evidence: bool
    metadata_matches_evidence: bool
    unsupported_citation_ids: list[str]
    unrelated_evidence: bool | None
    standard_number_correct: bool | None
    clause_number_correct: bool | None
    page_correct: bool | None
    source_document_correct: bool | None
    evidence_excerpt_matches: bool
    passed: bool


class AnswerCaseResult(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    question_id: str
    question: str
    ground_truth_status: GroundTruthStatus
    status: Literal["COMPLETED", "ERROR"]
    answer: str | None
    evidence_state: str | None
    citations: list[dict[str, Any]]
    evidence: list[dict[str, Any]]
    limitations: list[str]
    citation_evaluation: CitationEvaluation | None
    groundedness_review: GroundednessReview | None = None
    failure_categories: list[FailureCategory] = Field(default_factory=list)
    latency_ms: dict[str, float | None]
    usage: dict[str, int | None] | None
    provider: str | None
    model: str | None
    error_type: str | None = None
    error_message: str | None = None
