"""Stage 4.2 orchestration and validation for grounded BIS answers."""

from __future__ import annotations

import logging
import re
import time
from typing import Any, Callable, Literal, Mapping

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .evidence_context import EvidenceContextBuilder, EvidenceItem, EvidencePackage
from .evidence_sufficiency import (
    EvidenceAssessment,
    EvidenceSufficiencyGuard,
    supporting_context,
)
from .grounding import GROUNDING_SYSTEM_INSTRUCTION
from .llm_provider import (
    LLMProvider,
    ProviderConfigurationError,
    ProviderPermanentError,
    ProviderResponse,
    ProviderTransientError,
    ProviderUsage,
)
from .retrieval_service import RetrievalService
from .production import log_event

EvidenceState = Literal[
    "VERIFIED_EVIDENCE",
    "PARTIAL_EVIDENCE",
    "NO_VERIFIED_EVIDENCE",
]
MAX_GENERATION_ATTEMPTS = 2
NO_EVIDENCE_ANSWER = "No verified evidence was found for this query."
GUARDED_NO_EVIDENCE_ANSWER = (
    "No verified evidence was found in the available BIS sources for this query."
)
CITATION_RE = re.compile(r"\[(E[1-9]\d*)\]")


class AnswerValidationError(ValueError):
    """Provider output is syntactically or semantically unsafe."""

    def __init__(self, message: str, *, code: str = "unknown"):
        super().__init__(message)
        self.code = code


class AnswerGenerationError(RuntimeError):
    """Bounded generation attempts did not produce a valid answer."""


class ProviderAnswerDraft(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    answer: str = Field(min_length=1)
    evidence_state: Literal["VERIFIED_EVIDENCE", "PARTIAL_EVIDENCE"]
    citation_ids: list[str]
    limitations: list[str]


class AnswerCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_id: str
    standard_number: str | None
    clause_number: str | None
    clause_title: str | None
    document_id: str | None
    source_id: str | None
    title: str | None
    version: str | int | None
    page: int | None
    url: str | None


class AnswerObservability(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    provider: str | None
    model: str | None
    retrieval_ms: float | None
    context_ms: float
    generation_ms: float
    evidence_count: int
    evidence_state: EvidenceState
    attempts: int = Field(ge=0)
    usage: ProviderUsage | None
    assessment_state: Literal["SUFFICIENT", "PARTIAL", "INSUFFICIENT", "OUT_OF_DOMAIN"]
    assessment_ms: float = Field(ge=0)
    retrieval_candidate_count: int = Field(ge=0)
    supporting_evidence_count: int = Field(ge=0)
    gemini_call_avoided: bool


class GroundedAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    query: str
    answer: str
    evidence_state: EvidenceState
    citations: list[AnswerCitation]
    limitations: list[str]
    evidence: list[EvidenceItem]
    assessment: EvidenceAssessment
    metadata: AnswerObservability


class AnswerValidator:
    """Validate untrusted model output against the supplied evidence package."""

    def validate(
        self,
        response: ProviderResponse,
        package: EvidencePackage,
    ) -> tuple[ProviderAnswerDraft, list[AnswerCitation]]:
        try:
            draft = ProviderAnswerDraft.model_validate_json(response.text)
        except ValidationError as exc:
            raise AnswerValidationError(
                "provider output does not match answer schema", code="schema"
            ) from exc
        if not draft.answer.strip():
            raise AnswerValidationError("provider answer must not be blank", code="blank_answer")
        if not package.evidence:
            raise AnswerValidationError("provider must not be called without evidence", code="no_evidence")

        available = {item.evidence_id: item for item in package.evidence}
        if not draft.citation_ids:
            raise AnswerValidationError(
                "grounded answers require at least one citation", code="missing_citation"
            )
        if len(draft.citation_ids) != len(set(draft.citation_ids)):
            raise AnswerValidationError("citation IDs must be unique", code="duplicate_citation")
        unknown = [value for value in draft.citation_ids if value not in available]
        if unknown:
            raise AnswerValidationError(
                f"unsupported evidence citation(s): {', '.join(unknown)}",
                code="unsupported_citation",
            )

        answer_ids = CITATION_RE.findall(draft.answer)
        unknown_in_answer = sorted(set(answer_ids) - set(available))
        if unknown_in_answer:
            raise AnswerValidationError(
                f"answer references unsupported evidence: {', '.join(unknown_in_answer)}",
                code="unsupported_inline_citation",
            )
        missing_from_answer = [value for value in draft.citation_ids if value not in answer_ids]
        if missing_from_answer:
            # The IDs have already been checked against the retrieved evidence
            # above. Some Gemini structured responses return a valid
            # ``citation_ids`` list but omit its markers in prose; append only
            # those validated markers so the rendered answer stays traceable.
            draft = draft.model_copy(
                update={
                    "answer": f"{draft.answer.rstrip()} {' '.join(f'[{value}]' for value in missing_from_answer)}"
                }
            )
        if draft.evidence_state == "PARTIAL_EVIDENCE" and not any(
            value.strip() for value in draft.limitations
        ):
            raise AnswerValidationError(
                "partial evidence requires a limitation", code="partial_without_limitation"
            )

        citations = [self._citation(available[value]) for value in draft.citation_ids]
        return draft, citations

    @staticmethod
    def _citation(item: EvidenceItem) -> AnswerCitation:
        return AnswerCitation(
            evidence_id=item.evidence_id,
            standard_number=item.standard,
            clause_number=item.clause_number,
            clause_title=item.clause_title,
            document_id=item.document_id,
            source_id=item.source_id,
            title=item.title,
            version=item.version,
            page=item.page,
            url=item.url,
        )


class GroundedAnswerService:
    """Retrieval -> evidence package -> provider -> validated answer."""

    def __init__(
        self,
        retrieval_service: RetrievalService,
        context_builder: EvidenceContextBuilder,
        provider: LLMProvider | Callable[[], LLMProvider],
        *,
        validator: AnswerValidator | None = None,
        sufficiency_guard: EvidenceSufficiencyGuard | None = None,
        max_attempts: int = MAX_GENERATION_ATTEMPTS,
    ):
        if not isinstance(max_attempts, int) or isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be a positive integer")
        self.retrieval_service = retrieval_service
        self.context_builder = context_builder
        self.provider = provider
        self.validator = validator or AnswerValidator()
        self.sufficiency_guard = sufficiency_guard or EvidenceSufficiencyGuard()
        self.max_attempts = max_attempts

    def _provider(self) -> LLMProvider:
        return self.provider() if callable(self.provider) else self.provider

    def answer(
        self,
        query: str,
        *,
        top_k: int = 5,
        filters: Mapping[str, Any] | None = None,
    ) -> GroundedAnswer:
        retrieval = self.retrieval_service.retrieve(query, top_k=top_k, filters=filters)
        context_started = time.perf_counter()
        package = self.context_builder.build(retrieval.query, retrieval.results)
        context_ms = round((time.perf_counter() - context_started) * 1000, 3)
        assessment = self.sufficiency_guard.assess(package)
        retrieval_ms = (
            float(retrieval.timings["total_ms"])
            if retrieval.timings.get("total_ms") is not None
            else None
        )
        log_event(
            logging.INFO,
            "evidence_assessment",
            state=assessment.state,
            retrieval_ms=retrieval_ms,
            reranking_ms=retrieval.timings.get("reranking_ms"),
            candidates=assessment.signals.retrieval_candidate_count,
            supporting=len(assessment.supporting_evidence_ids),
            assessment_ms=assessment.assessment_ms,
        )
        if assessment.answer_evidence_state == "NO_VERIFIED_EVIDENCE":
            limitations = []
            if assessment.state == "OUT_OF_DOMAIN":
                limitations.append(
                    "The retrieved BIS sources are not sufficiently related to the question."
                )
            else:
                limitations.append(
                    "The available BIS sources did not supply sufficient verified evidence."
                )
            if package.omitted_count:
                limitations.append(
                    "Retrieved evidence could not be included within the configured context budget."
                )
            return GroundedAnswer(
                query=package.query,
                answer=(
                    NO_EVIDENCE_ANSWER
                    if not package.evidence
                    else GUARDED_NO_EVIDENCE_ANSWER
                ),
                evidence_state="NO_VERIFIED_EVIDENCE",
                citations=[],
                limitations=limitations,
                evidence=[],
                assessment=assessment,
                metadata=AnswerObservability(
                    provider=None,
                    model=None,
                    retrieval_ms=retrieval_ms,
                    context_ms=context_ms,
                    generation_ms=0.0,
                    evidence_count=0,
                    evidence_state="NO_VERIFIED_EVIDENCE",
                    attempts=0,
                    usage=None,
                    assessment_state=assessment.state,
                    assessment_ms=assessment.assessment_ms,
                    retrieval_candidate_count=assessment.signals.retrieval_candidate_count,
                    supporting_evidence_count=0,
                    gemini_call_avoided=True,
                ),
            )

        provider = self._provider()
        provider_context = supporting_context(package, assessment.supporting_evidence_ids)
        supported_ids = set(assessment.supporting_evidence_ids)
        supported_evidence = [
            item for item in package.evidence if item.evidence_id in supported_ids
        ]
        generation_started = time.perf_counter()
        last_error: Exception | None = None
        attempts = 0
        for attempts in range(1, self.max_attempts + 1):
            try:
                provider_response = provider.generate(
                    GROUNDING_SYSTEM_INSTRUCTION,
                    package.query,
                    provider_context,
                )
                draft, citations = self.validator.validate(provider_response, package)
                unsupported_citations = [
                    item.evidence_id for item in citations if item.evidence_id not in supported_ids
                ]
                if unsupported_citations:
                    raise AnswerValidationError(
                        "answer cites evidence rejected by the sufficiency guard",
                        code="guard_rejected_citation",
                    )
                generation_ms = round(
                    (time.perf_counter() - generation_started) * 1000, 3
                )
                final_state: EvidenceState = draft.evidence_state
                limitations = [value for value in draft.limitations if value.strip()]
                if assessment.state == "PARTIAL":
                    final_state = "PARTIAL_EVIDENCE"
                    if not limitations:
                        limitations.append(
                            "The available BIS evidence supports only part of the question."
                        )
                answer = GroundedAnswer(
                    query=package.query,
                    answer=draft.answer,
                    evidence_state=final_state,
                    citations=citations,
                    limitations=limitations,
                    evidence=supported_evidence,
                    assessment=assessment,
                    metadata=AnswerObservability(
                        provider=provider_response.provider,
                        model=provider_response.model,
                        retrieval_ms=retrieval_ms,
                        context_ms=context_ms,
                        generation_ms=generation_ms,
                        evidence_count=len(supported_evidence),
                        evidence_state=final_state,
                        attempts=attempts,
                        usage=provider_response.usage,
                        assessment_state=assessment.state,
                        assessment_ms=assessment.assessment_ms,
                        retrieval_candidate_count=assessment.signals.retrieval_candidate_count,
                        supporting_evidence_count=len(supported_evidence),
                        gemini_call_avoided=False,
                    ),
                )
                log_event(
                    logging.INFO,
                    "grounded_generation",
                    provider=provider_response.provider,
                    model=provider_response.model,
                    attempts=attempts,
                    generation_ms=generation_ms,
                    evidence_count=len(supported_evidence),
                    state=final_state,
                )
                return answer
            except (ProviderTransientError, AnswerValidationError) as exc:
                last_error = exc
                if attempts >= self.max_attempts:
                    break
            except (ProviderPermanentError, ProviderConfigurationError):
                raise

        log_event(
            logging.WARNING,
            "grounded_generation_failed",
            attempts=attempts,
            category=type(last_error).__name__ if last_error else "unknown",
            validation_code=(
                last_error.code if isinstance(last_error, AnswerValidationError) else None
            ),
            provider_failure_category=(
                last_error.category if isinstance(last_error, ProviderTransientError) else None
            ),
        )
        raise AnswerGenerationError(
            f"provider did not return a valid grounded answer after {attempts} attempts"
        ) from last_error
