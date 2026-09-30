"""Deterministic Stage 4.6 evidence sufficiency and domain-boundary guard.

The guard consumes only an existing Stage 4.1 :class:`EvidencePackage` and
configuration metadata. It performs no retrieval, embedding, generation, web
access, or corpus mutation.
"""

from __future__ import annotations

import re
import time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .config import PRODUCTS
from .evidence_context import EvidenceItem, EvidencePackage


AssessmentState = Literal["SUFFICIENT", "PARTIAL", "INSUFFICIENT", "OUT_OF_DOMAIN"]
AnswerEvidenceState = Literal[
    "VERIFIED_EVIDENCE", "PARTIAL_EVIDENCE", "NO_VERIFIED_EVIDENCE"
]

# Stage 4.4 v1.0.0 calibration: verified positive top scores ranged from
# 1.0696 upward; the sole candidate-returning negative scored -10.8490.
# Zero is deliberately a provisional floor inside a multi-signal rule, not a
# claimed general-purpose classifier threshold.
MIN_RERANKER_SUPPORT_SCORE = 0.0
STRONG_RERANKER_SUPPORT_SCORE = 3.0
CALIBRATION_VERSION = "stage-4.4-dataset-1.0.0"

STANDARD_RE = re.compile(r"(?<![A-Z0-9])IS\s*[-/]?\s*(\d+)(?:\s*[:(]\s*(\d{4})\)?)?\b", re.I)
CLAUSE_RE = re.compile(r"\b(?:clause|cl\.?)[\s:-]*(\d+(?:\.\d+)*)\b", re.I)
STRUCTURE_RE = re.compile(r"\b(annex|table)\s*[-:]?\s*([A-Z0-9.-]+)\b", re.I)
TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
COMPOUND_RE = re.compile(r"\b(?:and|plus|also)\b", re.I)

# Grammatical scaffolding is removed so overlap reflects subject terms. This
# is intentionally not an out-of-domain topic blacklist.
STOP_WORDS = frozenset(
    {
        "a", "an", "and", "are", "as", "at", "be", "by", "can", "do", "does",
        "for", "from", "how", "in", "is", "it", "of", "on", "or", "that", "the",
        "this", "to", "under", "what", "when", "where", "which", "who", "with",
        "apply", "applies", "require", "required", "requirement", "requirements",
        "specified", "specify", "about", "according",
        "establish", "established",
    }
)


class EvidenceSignals(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    retrieval_candidate_count: int = Field(ge=0)
    included_evidence_count: int = Field(ge=0)
    positive_reranker_count: int = Field(ge=0)
    bm25_support_count: int = Field(ge=0)
    lexical_support_count: int = Field(ge=0)
    semantic_support_count: int = Field(ge=0)
    top_reranker_score: float | None
    query_term_count: int = Field(ge=0)
    matched_query_terms: list[str]
    exact_standard_identifiers: list[str]
    matched_standard_identifiers: list[str]
    exact_clause_identifiers: list[str]
    matched_clause_identifiers: list[str]
    exact_structural_identifiers: list[str]
    matched_structural_identifiers: list[str]
    configured_product_ids: list[str]
    matched_product_ids: list[str]
    query_segment_count: int = Field(ge=0)
    supported_segment_count: int = Field(ge=0)
    context_omitted_count: int = Field(ge=0)


class EvidenceAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    state: AssessmentState
    answer_evidence_state: AnswerEvidenceState
    reason: str
    supporting_evidence_ids: list[str]
    signal_types: list[str]
    signals: EvidenceSignals
    assessment_ms: float = Field(ge=0)
    calibration_version: str = CALIBRATION_VERSION


def answer_state_for_assessment(state: AssessmentState) -> AnswerEvidenceState:
    return {
        "SUFFICIENT": "VERIFIED_EVIDENCE",
        "PARTIAL": "PARTIAL_EVIDENCE",
        "INSUFFICIENT": "NO_VERIFIED_EVIDENCE",
        "OUT_OF_DOMAIN": "NO_VERIFIED_EVIDENCE",
    }[state]  # type: ignore[return-value]


def _tokens(value: str) -> set[str]:
    values: set[str] = set()
    for raw in TOKEN_RE.findall(value):
        token = raw.casefold()
        if token in STOP_WORDS:
            continue
        if len(token) > 4 and token.endswith("s") and not token.endswith("ss"):
            token = token[:-1]
        if len(token) > 1 and token not in STOP_WORDS:
            values.add(token)
    return values


def _evidence_text(item: EvidenceItem) -> str:
    return "\n".join(
        str(value)
        for value in (
            item.standard,
            item.clause_number,
            item.clause_title,
            item.title,
            item.context_prefix,
            item.authoritative_text,
        )
        if value is not None
    )


def _standard_identifiers(query: str) -> list[str]:
    values = []
    for number, year in STANDARD_RE.findall(query):
        values.append(f"IS {number}:{year}" if year else f"IS {number}")
    return list(dict.fromkeys(values))


def _standard_matches(requested: str, actual: str | None) -> bool:
    if actual is None:
        return False
    requested_number = re.search(r"\d+", requested)
    actual_match = re.fullmatch(r"IS\s*(\d+):([0-9]{4})", actual, re.I)
    if requested_number is None or actual_match is None:
        return False
    if requested_number.group() != actual_match.group(1):
        return False
    requested_year = re.search(r":([0-9]{4})$", requested)
    return requested_year is None or requested_year.group(1) == actual_match.group(2)


def _products_in_query(query: str) -> list[str]:
    folded = " ".join(query.casefold().split())
    matched: list[str] = []
    for product_id, product in PRODUCTS.items():
        phrases = {product.label.casefold(), *(value.casefold() for value in product.keywords)}
        # Numeric-only keywords are identifiers, not product names.
        if any(
            phrase and not phrase.isdigit() and re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", folded)
            for phrase in phrases
        ):
            matched.append(product_id)
    return matched


def _matched_segments(query: str, evidence_terms: set[str]) -> tuple[int, int]:
    raw_segments = [value.strip(" ,;:?") for value in COMPOUND_RE.split(query)]
    segments = [_tokens(value) for value in raw_segments]
    segments = [value for value in segments if value]
    supported = 0
    for terms in segments:
        overlap = terms & evidence_terms
        required = len(terms) if len(terms) <= 3 else max(1, (len(terms) + 1) // 2)
        if len(overlap) >= required:
            supported += 1
    return len(segments), supported


def supporting_context(package: EvidencePackage, evidence_ids: list[str]) -> str:
    """Return exact existing context blocks for supported IDs without rewriting text."""

    allowed = set(evidence_ids)
    if not allowed or not package.context_text:
        return ""
    positions = [
        (item.evidence_id, package.context_text.find(f"[{item.evidence_id}]"))
        for item in package.evidence
    ]
    blocks: list[str] = []
    for index, (evidence_id, start) in enumerate(positions):
        if start < 0:
            continue
        end = positions[index + 1][1] if index + 1 < len(positions) else len(package.context_text)
        if evidence_id in allowed:
            blocks.append(package.context_text[start:end].strip())
    return "\n\n".join(blocks)


class EvidenceSufficiencyGuard:
    """Classify an immutable EvidencePackage using reproducible existing signals."""

    def assess(self, package: EvidencePackage) -> EvidenceAssessment:
        started = time.perf_counter()
        query = package.query
        query_terms = _tokens(query)
        exact_standards = _standard_identifiers(query)
        exact_clauses = list(dict.fromkeys(CLAUSE_RE.findall(query)))
        exact_structures = [
            f"{kind.title()} {identifier.upper()}"
            for kind, identifier in STRUCTURE_RE.findall(query)
        ]
        products = _products_in_query(query)

        evidence_terms_by_id = {
            item.evidence_id: _tokens(_evidence_text(item)) for item in package.evidence
        }
        all_evidence_terms = set().union(*evidence_terms_by_id.values()) if evidence_terms_by_id else set()
        matched_terms = sorted(query_terms & all_evidence_terms)
        identifier_terms = _tokens(
            " ".join([*exact_standards, *exact_clauses, *exact_structures])
        ) | {"clause", "annex", "table"}
        substantive_query_terms = query_terms - identifier_terms
        matched_substantive_terms = substantive_query_terms & all_evidence_terms
        matched_standards = [
            value
            for value in exact_standards
            if any(_standard_matches(value, item.standard) for item in package.evidence)
        ]
        matched_clauses = [
            value
            for value in exact_clauses
            if any(item.clause_number == value for item in package.evidence)
        ]
        matched_structures = [
            value
            for value in exact_structures
            if any(value.casefold() in _evidence_text(item).casefold() for item in package.evidence)
        ]
        matched_products = [
            product_id
            for product_id in products
            if any(
                item.standard in PRODUCTS[product_id].standard_numbers
                for item in package.evidence
            )
        ]

        positive = [
            item for item in package.evidence
            if item.reranker_score >= MIN_RERANKER_SUPPORT_SCORE
        ]
        bm25 = [item for item in package.evidence if "bm25" in item.retrieval_methods]
        semantic = [item for item in package.evidence if "semantic" in item.retrieval_methods]
        lexical = [
            item for item in package.evidence
            if query_terms & evidence_terms_by_id[item.evidence_id]
        ]
        exact_ids = {
            item.evidence_id
            for item in package.evidence
            if any(_standard_matches(value, item.standard) for value in matched_standards)
            or item.clause_number in matched_clauses
            or any(value.casefold() in _evidence_text(item).casefold() for value in matched_structures)
        }
        product_ids = {
            item.evidence_id
            for item in package.evidence
            if any(item.standard in PRODUCTS[value].standard_numbers for value in matched_products)
        }
        supporting = []
        for item in package.evidence:
            overlap = bool(query_terms & evidence_terms_by_id[item.evidence_id])
            multi_signal = item.reranker_score >= MIN_RERANKER_SUPPORT_SCORE and (
                "bm25" in item.retrieval_methods or overlap or item.evidence_id in product_ids
            )
            high_semantic = (
                item.reranker_score >= STRONG_RERANKER_SUPPORT_SCORE
                and "semantic" in item.retrieval_methods
            )
            if item.evidence_id in exact_ids or multi_signal or high_semantic:
                supporting.append(item.evidence_id)

        segment_count, supported_segments = _matched_segments(query, all_evidence_terms)
        signals = EvidenceSignals(
            retrieval_candidate_count=package.evidence_count + package.omitted_count,
            included_evidence_count=package.evidence_count,
            positive_reranker_count=len(positive),
            bm25_support_count=len(bm25),
            lexical_support_count=len(lexical),
            semantic_support_count=len(semantic),
            top_reranker_score=(max((item.reranker_score for item in package.evidence), default=None)),
            query_term_count=len(query_terms),
            matched_query_terms=matched_terms,
            exact_standard_identifiers=exact_standards,
            matched_standard_identifiers=matched_standards,
            exact_clause_identifiers=exact_clauses,
            matched_clause_identifiers=matched_clauses,
            exact_structural_identifiers=exact_structures,
            matched_structural_identifiers=matched_structures,
            configured_product_ids=products,
            matched_product_ids=matched_products,
            query_segment_count=segment_count,
            supported_segment_count=supported_segments,
            context_omitted_count=package.omitted_count,
        )

        signal_types = []
        if matched_standards or matched_clauses or matched_structures:
            signal_types.append("exact_identifier")
        if matched_products:
            signal_types.append("configured_product")
        if bm25:
            signal_types.append("bm25")
        if lexical:
            signal_types.append("lexical_overlap")
        if positive:
            signal_types.append("positive_reranker")
        if semantic:
            signal_types.append("semantic")

        if not package.evidence:
            state: AssessmentState = "INSUFFICIENT"
            reason = "empty_evidence"
        elif exact_standards and not matched_standards:
            state = "INSUFFICIENT"
            reason = "unresolved_exact_standard"
            supporting = []
        elif exact_clauses and not matched_clauses:
            if matched_standards and supporting:
                state = "PARTIAL"
                reason = "unresolved_exact_clause"
            else:
                state = "INSUFFICIENT"
                reason = "unresolved_exact_clause"
                supporting = []
        elif (
            (matched_standards or matched_clauses or matched_structures)
            and substantive_query_terms
            and not matched_substantive_terms
            and not matched_products
        ):
            state = "PARTIAL"
            reason = "exact_identifier_only_support"
        elif segment_count >= 2 and 0 < supported_segments < segment_count and supporting:
            state = "PARTIAL"
            reason = "compound_query_partially_supported"
        elif package.omitted_count and supporting:
            state = "PARTIAL"
            reason = "context_budget_omitted_evidence"
        elif supporting:
            state = "SUFFICIENT"
            reason = (
                "exact_identifier_match"
                if matched_standards or matched_clauses or matched_structures
                else "multi_signal_support"
            )
        elif not bm25 and not lexical and not matched_products and not exact_standards and not exact_clauses:
            state = "OUT_OF_DOMAIN"
            reason = "no_domain_alignment"
        else:
            state = "INSUFFICIENT"
            reason = "weak_evidence_support"

        elapsed = round((time.perf_counter() - started) * 1000, 3)
        return EvidenceAssessment(
            state=state,
            answer_evidence_state=answer_state_for_assessment(state),
            reason=reason,
            supporting_evidence_ids=supporting,
            signal_types=signal_types,
            signals=signals,
            assessment_ms=elapsed,
        )
