"""Stage 4.4 reproducible evaluation of the frozen retrieval and answer pipeline.

This module is a consumer of production services. It never mutates ranking,
corpus, model, evidence, grounding, validation, or API behavior.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import platform
import re
import statistics
import tempfile
import time
from collections import Counter
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Any, Iterable, Sequence

from pydantic import ValidationError

from .config import Settings, resolve_embedding_spec
from .evaluation_models import (
    DATASET_VERSION,
    AnswerCaseResult,
    CitationEvaluation,
    EvaluationQuestion,
    FailureCategory,
    LayerEvaluation,
    LayerResult,
    RetrievalCaseResult,
)
from .evidence_context import EvidenceContextBuilder
from .grounded_answer import GroundedAnswerService
from .hybrid_retrieval import (
    DEFAULT_BM25_B,
    DEFAULT_BM25_K1,
    DEFAULT_CANDIDATE_K,
    DEFAULT_FINAL_K,
    DEFAULT_RERANK_K,
    DEFAULT_RRF_K,
    INDEXED_FIELDS,
    RERANKER_MODEL,
    RERANKER_REVISION,
    reciprocal_rank_fusion,
)
from .llm_provider import DEFAULT_GEMINI_MODEL, LLMProvider
from .retrieval_service import EvidenceResult, RetrievalService
from .utils import atomic_json, sha256_file, utc_now

EVALUATION_VERSION = "4.4.0"
QUESTION_FILE = "questions.jsonl"
RETRIEVAL_RESULTS_FILE = "retrieval_results.jsonl"
ANSWER_RESULTS_FILE = "answer_results.jsonl"
REPORT_FILE = "evaluation_report.json"
FAILURES_FILE = "failure_analysis.json"
EXACT_IDENTIFIER_RE = re.compile(r"(?<![A-Z0-9])IS\s*[-/]?\s*(\d+)(?::(\d{4}))?\b", re.I)


class EvaluationError(ValueError):
    """Invalid benchmark input or an impossible evaluation request."""


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise EvaluationError(f"invalid JSON at {path}:{line_number}") from exc
    return rows


def load_questions(
    path: Path,
    *,
    valid_chunk_ids: set[str] | None = None,
) -> list[EvaluationQuestion]:
    if not path.exists():
        raise EvaluationError(f"evaluation dataset not found: {path}")
    questions: list[EvaluationQuestion] = []
    ids: set[str] = set()
    for line_number, value in enumerate(_jsonl(path), start=1):
        try:
            question = EvaluationQuestion.model_validate(value)
        except ValidationError as exc:
            raise EvaluationError(f"invalid question at {path}:{line_number}: {exc}") from exc
        if question.question_id in ids:
            raise EvaluationError(f"duplicate question_id: {question.question_id}")
        ids.add(question.question_id)
        if valid_chunk_ids is not None and question.expected_chunk_ids is not None:
            unknown = sorted(set(question.expected_chunk_ids) - valid_chunk_ids)
            if unknown:
                raise EvaluationError(
                    f"{question.question_id} references unknown chunk IDs: {', '.join(unknown)}"
                )
        questions.append(question)
    return questions


def select_questions(
    questions: Sequence[EvaluationQuestion],
    *,
    question_ids: Sequence[str] | None = None,
    categories: Sequence[str] | None = None,
    limit: int | None = None,
) -> list[EvaluationQuestion]:
    selected = list(questions)
    if question_ids:
        requested = set(question_ids)
        known = {question.question_id for question in questions}
        unknown = sorted(requested - known)
        if unknown:
            raise EvaluationError(f"unknown question ID(s): {', '.join(unknown)}")
        selected = [question for question in selected if question.question_id in requested]
    if categories:
        requested_categories = set(categories)
        known_categories = {question.question_type for question in questions}
        unknown = sorted(requested_categories - known_categories)
        if unknown:
            raise EvaluationError(f"unknown category/categories: {', '.join(unknown)}")
        selected = [question for question in selected if question.question_type in requested_categories]
    if limit is not None:
        if isinstance(limit, bool) or limit < 1:
            raise EvaluationError("limit must be a positive integer")
        selected = selected[:limit]
    return selected


def recall_at_k(ranked_ids: Sequence[str], expected_ids: Sequence[str] | None, k: int) -> float | None:
    if not expected_ids:
        return None
    if k < 1:
        raise EvaluationError("k must be positive")
    return len(set(ranked_ids[:k]) & set(expected_ids)) / len(set(expected_ids))


def reciprocal_rank(ranked_ids: Sequence[str], expected_ids: Sequence[str] | None) -> float | None:
    if not expected_ids:
        return None
    expected = set(expected_ids)
    for rank, chunk_id in enumerate(ranked_ids, start=1):
        if chunk_id in expected:
            return 1.0 / rank
    return 0.0


def _write_jsonl(path: Path, values: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=path.name, dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            for value in values:
                handle.write(json.dumps(value, ensure_ascii=False, sort_keys=True))
                handle.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _record_layer_result(rank: int, chunk_id: str, record: dict, score: float | None) -> LayerResult:
    pages: list[int] = []
    for value in record.get("pages") or []:
        try:
            pages.append(int(value))
        except (TypeError, ValueError):
            continue
    return LayerResult(
        rank=rank,
        chunk_id=chunk_id,
        document_id=record.get("document_id"),
        standard_numbers=[str(value) for value in (record.get("standard_numbers") or [])],
        clause_number=record.get("clause_number"),
        annex_identifier=record.get("annex_identifier"),
        table_identifier=record.get("table_identifier"),
        pages=pages,
        score=score,
    )


def _evidence_layer_result(result: EvidenceResult) -> LayerResult:
    return LayerResult(
        rank=result.rank,
        chunk_id=result.chunk_id,
        document_id=result.source.document_id,
        standard_numbers=[result.standard_number] if result.standard_number else [],
        clause_number=result.clause_number,
        annex_identifier=None,
        table_identifier=None,
        pages=[result.source.page] if result.source.page is not None else [],
        score=result.retrieval.reranker_score,
    )


def _metrics(question: EvaluationQuestion, results: Sequence[LayerResult]) -> dict[str, float | bool | int | None]:
    ranked = [result.chunk_id for result in results]
    standards = {value for result in results for value in result.standard_numbers}
    documents = {result.document_id for result in results if result.document_id}
    clauses = {result.clause_number for result in results if result.clause_number}
    return {
        "recall_at_1": recall_at_k(ranked, question.expected_chunk_ids, 1),
        "recall_at_3": recall_at_k(ranked, question.expected_chunk_ids, 3),
        "recall_at_5": recall_at_k(ranked, question.expected_chunk_ids, 5),
        "recall_at_10": recall_at_k(ranked, question.expected_chunk_ids, 10) if len(results) >= 10 else None,
        "mrr": reciprocal_rank(ranked, question.expected_chunk_ids),
        "expected_document_retrieved": (
            bool(documents & set(question.expected_documents)) if question.expected_documents else None
        ),
        "expected_standard_retrieved": (
            bool(standards & set(question.expected_standard_numbers))
            if question.expected_standard_numbers else None
        ),
        "expected_clause_retrieved": (
            bool(clauses & set(question.expected_clause_numbers))
            if question.expected_clause_numbers else None
        ),
        "returned_count": len(results),
    }


def _retrieval_failures(
    question: EvaluationQuestion,
    final_results: Sequence[LayerResult],
    rrf_results: Sequence[LayerResult],
) -> list[FailureCategory]:
    if question.ground_truth_status != "VERIFIED" or not question.expected_chunk_ids:
        return []
    final_ids = {result.chunk_id for result in final_results}
    expected_ids = set(question.expected_chunk_ids)
    if final_ids & expected_ids:
        return []
    standards = {value for result in final_results for value in result.standard_numbers}
    documents = {result.document_id for result in final_results if result.document_id}
    clauses = {result.clause_number for result in final_results if result.clause_number}
    if question.expected_standard_numbers and not standards.intersection(question.expected_standard_numbers):
        return ["WRONG_STANDARD"]
    if question.expected_documents and not documents.intersection(question.expected_documents):
        return ["WRONG_DOCUMENT"]
    if question.expected_clause_numbers and not clauses.intersection(question.expected_clause_numbers):
        return ["WRONG_CLAUSE"]
    if question.question_type == "table":
        return ["WRONG_TABLE"]
    if question.question_type == "annex":
        return ["WRONG_ANNEX"]
    if expected_ids.intersection(result.chunk_id for result in rrf_results):
        return ["RERANKING_ERROR"]
    return ["RETRIEVAL_MISS"]


def evaluate_retrieval_case(
    question: EvaluationQuestion,
    service: RetrievalService,
) -> RetrievalCaseResult:
    hybrid = service._retriever  # evaluation-only observation of frozen layers
    filters = question.filters or None

    started = time.perf_counter()
    bm25 = hybrid.bm25.search(question.question, top_k=DEFAULT_CANDIDATE_K, filters=filters)
    bm25_ms = round((time.perf_counter() - started) * 1000, 3)
    bm25_results = [
        _record_layer_result(item.rank, item.chunk_id, item.metadata, item.bm25_score)
        for item in bm25[:10]
    ]

    started = time.perf_counter()
    semantic = hybrid.semantic.search(question.question, top_k=DEFAULT_CANDIDATE_K, filters=filters)
    semantic_ms = round((time.perf_counter() - started) * 1000, 3)
    semantic_results = [
        _record_layer_result(item.rank, item.chunk_id, item.to_dict(), item.similarity_score)
        for item in semantic[:10]
    ]

    started = time.perf_counter()
    fused = reciprocal_rank_fusion(semantic, bm25, rrf_k=DEFAULT_RRF_K)
    rrf_ms = round((time.perf_counter() - started) * 1000, 3)
    rrf_results = [
        _record_layer_result(rank, item.chunk_id, item.record, item.fusion_score)
        for rank, item in enumerate(fused[:10], start=1)
    ]

    wall_started = time.perf_counter()
    final = service.retrieve(question.question, top_k=DEFAULT_FINAL_K, filters=filters)
    wall_ms = round((time.perf_counter() - wall_started) * 1000, 3)
    final_results = [_evidence_layer_result(item) for item in final.results]
    final_ms = float(final.timings.get("total_ms", wall_ms))
    layers = {
        "bm25": LayerEvaluation(latency_ms=bm25_ms, results=bm25_results),
        "semantic": LayerEvaluation(latency_ms=semantic_ms, results=semantic_results),
        "rrf_hybrid": LayerEvaluation(latency_ms=rrf_ms, results=rrf_results),
        "hybrid_reranked": LayerEvaluation(latency_ms=final_ms, results=final_results),
    }
    metrics = _metrics(question, final_results)
    for key in ("bm25_ms", "semantic_ms", "fusion_ms", "reranking_ms", "total_ms"):
        metrics[key] = float(final.timings[key]) if key in final.timings else None

    exact: dict[str, Any] | None = None
    if EXACT_IDENTIFIER_RE.search(question.question):
        repeated = service.retrieve(question.question, top_k=DEFAULT_FINAL_K, filters=filters)
        first_ids = [item.chunk_id for item in final.results]
        repeated_ids = [item.chunk_id for item in repeated.results]
        expected_standards = set(question.expected_standard_numbers or [])
        returned_standards = {item.standard_number for item in final.results if item.standard_number}
        exact = {
            "expected_standard_numbers": sorted(expected_standards),
            "returned_standard_numbers": sorted(returned_standards),
            "unrelated_standard_present": bool(returned_standards - expected_standards) if expected_standards else None,
            "deterministic_result_ids": first_ids == repeated_ids,
            "result_ids": first_ids,
            "filters": question.filters,
        }

    return RetrievalCaseResult(
        question_id=question.question_id,
        question=question.question,
        question_type=question.question_type,
        ground_truth_status=question.ground_truth_status,
        filters=question.filters,
        layers=layers,
        metrics=metrics,
        exact_identifier=exact,
        failure_categories=_retrieval_failures(question, final_results, rrf_results),
    )


def evaluate_citations(question: EvaluationQuestion, answer: Any) -> CitationEvaluation:
    evidence = {item.evidence_id: item for item in answer.evidence}
    citation_ids = [item.evidence_id for item in answer.citations]
    unsupported = [value for value in citation_ids if value not in evidence]
    metadata_matches = True
    excerpt_matches = True
    cited_chunks: list[str] = []
    for citation in answer.citations:
        item = evidence.get(citation.evidence_id)
        if item is None:
            metadata_matches = False
            excerpt_matches = False
            continue
        cited_chunks.append(item.chunk_id)
        metadata_matches = metadata_matches and all(
            (
                citation.standard_number == item.standard,
                citation.clause_number == item.clause_number,
                citation.clause_title == item.clause_title,
                citation.document_id == item.document_id,
                citation.source_id == item.source_id,
                citation.title == item.title,
                citation.version == item.version,
                citation.page == item.page,
                citation.url == item.url,
            )
        )
        excerpt_matches = excerpt_matches and bool(item.authoritative_text)

    requires = answer.evidence_state != "NO_VERIFIED_EVIDENCE"
    exists = bool(answer.citations) if requires else not answer.citations
    exhaustive = question.expected_chunks_exhaustive and question.expected_chunk_ids is not None
    unrelated = (
        bool(set(cited_chunks) - set(question.expected_chunk_ids or [])) if exhaustive else None
    )

    def expected_match(field: str, expected: Sequence[Any] | None) -> bool | None:
        if not expected:
            return None
        values = [getattr(citation, field) for citation in answer.citations]
        return bool(set(values) & set(expected))

    standard = expected_match("standard_number", question.expected_standard_numbers)
    clause = expected_match("clause_number", question.expected_clause_numbers)
    page = expected_match("page", question.expected_pages)
    document = expected_match("document_id", question.expected_documents)
    checks = [exists, not unsupported, metadata_matches, excerpt_matches]
    checks.extend(value for value in (standard, clause, page, document) if value is not None)
    if unrelated is not None:
        checks.append(not unrelated)
    return CitationEvaluation(
        citation_exists_when_required=exists,
        ids_correspond_to_evidence=not unsupported,
        metadata_matches_evidence=metadata_matches,
        unsupported_citation_ids=unsupported,
        unrelated_evidence=unrelated,
        standard_number_correct=standard,
        clause_number_correct=clause,
        page_correct=page,
        source_document_correct=document,
        evidence_excerpt_matches=excerpt_matches,
        passed=all(checks),
    )


def evaluate_answer_case(
    question: EvaluationQuestion,
    service: GroundedAnswerService,
) -> AnswerCaseResult:
    started = time.perf_counter()
    try:
        answer = service.answer(question.question, filters=question.filters)
        total_ms = round((time.perf_counter() - started) * 1000, 3)
        citations = evaluate_citations(question, answer)
        failures: list[FailureCategory] = []
        if not citations.passed:
            failures.append("CITATION_ERROR")
        if (
            question.expected_evidence_state == "NO_VERIFIED_EVIDENCE"
            and answer.evidence_state != "NO_VERIFIED_EVIDENCE"
        ):
            failures.append("INSUFFICIENT_EVIDENCE_HANDLING")
        usage = answer.metadata.usage.model_dump() if answer.metadata.usage else None
        return AnswerCaseResult(
            question_id=question.question_id,
            question=question.question,
            ground_truth_status=question.ground_truth_status,
            status="COMPLETED",
            answer=answer.answer,
            evidence_state=answer.evidence_state,
            citations=[item.model_dump(mode="json") for item in answer.citations],
            evidence=[item.model_dump(mode="json") for item in answer.evidence],
            limitations=list(answer.limitations),
            citation_evaluation=citations,
            groundedness_review=None,
            failure_categories=failures,
            latency_ms={
                "retrieval": answer.metadata.retrieval_ms,
                "context_building": answer.metadata.context_ms,
                "generation": answer.metadata.generation_ms,
                "total": total_ms,
            },
            usage=usage,
            provider=answer.metadata.provider,
            model=answer.metadata.model,
        )
    except Exception as exc:  # result recording must survive individual failures
        return AnswerCaseResult(
            question_id=question.question_id,
            question=question.question,
            ground_truth_status=question.ground_truth_status,
            status="ERROR",
            answer=None,
            evidence_state=None,
            citations=[],
            evidence=[],
            limitations=[],
            citation_evaluation=None,
            groundedness_review=None,
            failure_categories=["GENERATION_ERROR"],
            latency_ms={"retrieval": None, "context_building": None, "generation": None, "total": round((time.perf_counter() - started) * 1000, 3)},
            usage=None,
            provider=None,
            model=None,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )


def _aggregate_values(values: Sequence[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "p95": None, "min": None, "max": None}
    ordered = sorted(values)
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 6),
        "median": round(statistics.median(values), 6),
        "p95": round(ordered[max(0, math.ceil(0.95 * len(ordered)) - 1)], 6),
        "min": round(ordered[0], 6),
        "max": round(ordered[-1], 6),
    }


def _rate(values: Sequence[bool]) -> dict[str, float | int | None]:
    return {
        "matched": sum(values),
        "evaluated_questions": len(values),
        "rate": round(sum(values) / len(values), 6) if values else None,
    }


def _tree_hash(path: Path) -> str | None:
    if not path.exists():
        return None
    digest = hashlib.sha256()
    for file in sorted(item for item in path.rglob("*") if item.is_file()):
        digest.update(str(file.relative_to(path)).encode())
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(file)))
    return digest.hexdigest()


def _package_version(name: str) -> str | None:
    try:
        return version(name)
    except PackageNotFoundError:
        return None


def _retrieval_summary(results: Sequence[RetrievalCaseResult], questions: dict[str, EvaluationQuestion]) -> dict[str, Any]:
    layer_names = ("bm25", "semantic", "rrf_hybrid", "hybrid_reranked")
    layers: dict[str, Any] = {}
    for layer in layer_names:
        recalls: dict[str, Any] = {}
        mrr_values: list[float] = []
        for k in (1, 3, 5, 10):
            values: list[float] = []
            for result in results:
                question = questions[result.question_id]
                ranked = [item.chunk_id for item in result.layers[layer].results]
                value = recall_at_k(ranked, question.expected_chunk_ids, k)
                if value is not None and not (layer == "hybrid_reranked" and k == 10):
                    values.append(value)
                rr = reciprocal_rank(ranked, question.expected_chunk_ids)
                if k == 1 and rr is not None:
                    mrr_values.append(rr)
            recalls[f"recall_at_{k}"] = {
                "mean": round(statistics.mean(values), 6) if values else None,
                "evaluated_questions": len(values),
                **({"note": "not available: frozen RetrievalService returns at most 5"} if layer == "hybrid_reranked" and k == 10 else {}),
            }
        layers[layer] = {
            **recalls,
            "mrr": {"mean": round(statistics.mean(mrr_values), 6) if mrr_values else None, "evaluated_questions": len(mrr_values)},
            "latency_ms": _aggregate_values([result.layers[layer].latency_ms for result in results]),
        }
    for name, metric in (
        ("expected_document_retrieval", "expected_document_retrieved"),
        ("expected_standard_retrieval", "expected_standard_retrieved"),
        ("expected_clause_retrieval", "expected_clause_retrieved"),
    ):
        layers["hybrid_reranked"][name] = _rate(
            [bool(result.metrics[metric]) for result in results if result.metrics.get(metric) is not None]
        )
    return layers


def _reproducibility(settings: Settings, dataset_path: Path, provider: LLMProvider | None) -> dict[str, Any]:
    chunks = settings.processed_dir / "chunks_v2" / "chunks.jsonl"
    embeddings = settings.embeddings_v2_dir / "embeddings.jsonl"
    vector_config = settings.vector_store_dir / "index_config.json"
    embedding = resolve_embedding_spec()
    return {
        "evaluation_version": EVALUATION_VERSION,
        "dataset_version": DATASET_VERSION,
        "dataset_sha256": sha256_file(dataset_path),
        "corpus_sha256": _tree_hash(settings.data_dir / "raw"),
        "chunks_sha256": sha256_file(chunks),
        "embeddings_sha256": sha256_file(embeddings),
        "vector_store_config_sha256": sha256_file(vector_config),
        "vector_store_configuration": json.loads(vector_config.read_text(encoding="utf-8")),
        "embedding": {"model": embedding.model_name, "revision": embedding.revision, "normalize": embedding.normalize, "pooling": embedding.pooling},
        "bm25": {"implementation": "in_repo_okapi_bm25", "k1": DEFAULT_BM25_K1, "b": DEFAULT_BM25_B, "indexed_fields": list(INDEXED_FIELDS)},
        "rrf": {"k": DEFAULT_RRF_K, "semantic_candidate_k": DEFAULT_CANDIDATE_K, "bm25_candidate_k": DEFAULT_CANDIDATE_K},
        "reranker": {"model": RERANKER_MODEL, "revision": RERANKER_REVISION, "rerank_k": DEFAULT_RERANK_K, "final_k": DEFAULT_FINAL_K},
        "llm": {"provider": getattr(provider, "provider_name", None), "model": getattr(provider, "model", os.environ.get("BIS_LLM_MODEL", DEFAULT_GEMINI_MODEL))},
        "runtime": {"python": platform.python_version(), "sentence_transformers": _package_version("sentence-transformers"), "torch": _package_version("torch"), "numpy": _package_version("numpy")},
    }


def run_evaluation(
    settings: Settings,
    *,
    output_dir: Path | None = None,
    questions_path: Path | None = None,
    question_ids: Sequence[str] | None = None,
    categories: Sequence[str] | None = None,
    limit: int | None = None,
    provider: LLMProvider | None = None,
    include_answers: bool = False,
) -> dict[str, Any]:
    output_dir = output_dir or settings.data_dir / "evaluation"
    questions_path = questions_path or settings.data_dir / "evaluation" / QUESTION_FILE
    chunks_path = settings.processed_dir / "chunks_v2" / "chunks.jsonl"
    chunk_ids = {str(row["chunk_id"]) for row in _jsonl(chunks_path)}
    all_questions = load_questions(questions_path, valid_chunk_ids=chunk_ids)
    questions = select_questions(all_questions, question_ids=question_ids, categories=categories, limit=limit)
    if not questions:
        raise EvaluationError("question selection is empty")
    if include_answers and provider is None:
        raise EvaluationError("answer evaluation requires a configured LLM provider")

    initialization_started = time.perf_counter()
    retrieval_service = RetrievalService.from_settings(settings)
    initialization_ms = round((time.perf_counter() - initialization_started) * 1000, 3)
    retrieval_results = [evaluate_retrieval_case(question, retrieval_service) for question in questions]
    _write_jsonl(output_dir / RETRIEVAL_RESULTS_FILE, [item.model_dump(mode="json") for item in retrieval_results])

    answer_results: list[AnswerCaseResult] = []
    if include_answers:
        answer_service = GroundedAnswerService(retrieval_service, EvidenceContextBuilder(), provider)  # type: ignore[arg-type]
        answer_questions = [question for question in questions if question.ground_truth_status == "VERIFIED"]
        answer_results = [evaluate_answer_case(question, answer_service) for question in answer_questions]
    _write_jsonl(output_dir / ANSWER_RESULTS_FILE, [item.model_dump(mode="json") for item in answer_results])

    question_map = {question.question_id: question for question in questions}
    categories_count = dict(sorted(Counter(question.question_type for question in questions).items()))
    failures = [
        {"question_id": result.question_id, "question": result.question, "categories": list(result.failure_categories), "stage": "retrieval"}
        for result in retrieval_results if result.failure_categories
    ] + [
        {"question_id": result.question_id, "question": result.question, "categories": list(result.failure_categories), "stage": "answer", "error_type": result.error_type}
        for result in answer_results if result.failure_categories
    ]
    failure_counts = Counter(category for item in failures for category in item["categories"])
    citation_values = [item.citation_evaluation.passed for item in answer_results if item.citation_evaluation is not None]
    no_evidence = [item for item in answer_results if question_map[item.question_id].expected_evidence_state == "NO_VERIFIED_EVIDENCE"]
    negative_retrieval = [
        item
        for item in retrieval_results
        if question_map[item.question_id].expected_evidence_state == "NO_VERIFIED_EVIDENCE"
    ]
    latency_fields = ("bm25_ms", "semantic_ms", "fusion_ms", "reranking_ms", "total_ms")
    report = {
        "stage": "4.4",
        "generated_at": utc_now(),
        "evaluation_mode": "retrieval_and_answers" if include_answers else "retrieval_only",
        "dataset": {
            "path": str(questions_path),
            "size": len(questions),
            "full_dataset_size": len(all_questions),
            "verified_ground_truth": sum(question.ground_truth_status == "VERIFIED" for question in questions),
            "unverified_ground_truth": sum(question.ground_truth_status == "UNVERIFIED" for question in questions),
            "categories": categories_count,
        },
        "retrieval": _retrieval_summary(retrieval_results, question_map),
        "exact_identifier": {
            "evaluated": sum(result.exact_identifier is not None for result in retrieval_results),
            "all_deterministic": all(result.exact_identifier["deterministic_result_ids"] for result in retrieval_results if result.exact_identifier is not None),
            "unrelated_standard_cases": sum(bool(result.exact_identifier["unrelated_standard_present"]) for result in retrieval_results if result.exact_identifier is not None),
        },
        "answers": {
            "status": "completed" if include_answers else "not_run_provider_not_requested",
            "evaluated": len(answer_results),
            "completed": sum(item.status == "COMPLETED" for item in answer_results),
            "errors": sum(item.status == "ERROR" for item in answer_results),
            "groundedness_review": {"reviewed": 0, "note": "Populate PASS, FAIL, or UNCERTAIN only after human review."},
        },
        "citations": {
            "evaluated": len(citation_values),
            "correct": sum(citation_values),
            "correctness_rate": round(sum(citation_values) / len(citation_values), 6) if citation_values else None,
        },
        "no_evidence_behavior": {
            "retrieval_cases": len(negative_retrieval),
            "retrieval_returned_zero": sum(
                not item.layers["hybrid_reranked"].results for item in negative_retrieval
            ),
            "retrieval_returned_candidates": sum(
                bool(item.layers["hybrid_reranked"].results) for item in negative_retrieval
            ),
            "answer_cases": len(no_evidence),
            "expected_state_returned": sum(item.evidence_state == "NO_VERIFIED_EVIDENCE" for item in no_evidence),
            "without_citations": sum(not item.citations for item in no_evidence),
            "note": (
                "Retrieval candidate counts are observations, not semantic answer judgments. "
                "Answer-state and citation checks require answer evaluation."
            ),
        },
        "latency_ms": {
            "service_initialization_process_cold": initialization_ms,
            "first_query_total": retrieval_results[0].metrics.get("total_ms"),
            "warm_queries": {
                field: _aggregate_values([float(item.metrics[field]) for item in retrieval_results[1:] if item.metrics.get(field) is not None])
                for field in latency_fields
            },
            "answer_total": _aggregate_values([float(item.latency_ms["total"]) for item in answer_results if item.latency_ms.get("total") is not None]),
            "note": "Cold start is process/model initialization; filesystem and OS caches may already be warm.",
        },
        "failures": {"count": len(failures), "categories": dict(sorted(failure_counts.items()))},
        "reproducibility": _reproducibility(settings, questions_path, provider),
        "no_overall_rag_accuracy": True,
    }
    atomic_json(output_dir / REPORT_FILE, report)
    atomic_json(output_dir / FAILURES_FILE, {"generated_at": report["generated_at"], "failures": failures, "counts": dict(sorted(failure_counts.items()))})
    return report
