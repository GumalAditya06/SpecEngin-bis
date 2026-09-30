"""Separate Stage 4.6 evaluation over the frozen Stage 4.4 dataset and retrieval."""

from __future__ import annotations

import json
import statistics
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Settings
from .evidence_context import EvidenceContextBuilder
from .evidence_sufficiency import (
    CALIBRATION_VERSION,
    MIN_RERANKER_SUPPORT_SCORE,
    EvidenceSufficiencyGuard,
)
from .rag_evaluation import load_questions
from .retrieval_service import RetrievalService
from .utils import atomic_json, sha256_file, utc_now


REPORT_FILE = "stage_4_6_evaluation_report.json"


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _latency(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "mean": None, "median": None, "max": None}
    return {
        "count": len(values),
        "mean": round(statistics.mean(values), 6),
        "median": round(statistics.median(values), 6),
        "max": round(max(values), 6),
    }


def run_sufficiency_evaluation(
    settings: Settings,
    *,
    output_path: Path | None = None,
) -> dict[str, Any]:
    evaluation_dir = settings.data_dir / "evaluation"
    questions_path = evaluation_dir / "questions.jsonl"
    baseline_path = evaluation_dir / "retrieval_results.jsonl"
    questions = load_questions(questions_path)
    baseline_rows = _load_jsonl(baseline_path)
    baseline = {row["question_id"]: row for row in baseline_rows}
    if set(baseline) != {question.question_id for question in questions}:
        raise ValueError("Stage 4.4 baseline does not cover the current evaluation dataset")

    positive_top_scores: list[float] = []
    negative_top_scores: list[float] = []
    for question in questions:
        results = baseline[question.question_id]["layers"]["hybrid_reranked"]["results"]
        if not results:
            continue
        top = float(results[0]["score"])
        if question.ground_truth_status == "VERIFIED" and question.expected_chunk_ids:
            positive_top_scores.append(top)
        elif question.expected_evidence_state == "NO_VERIFIED_EVIDENCE":
            negative_top_scores.append(top)

    service = RetrievalService.from_settings(settings)
    builder = EvidenceContextBuilder()
    guard = EvidenceSufficiencyGuard()
    cases: list[dict[str, Any]] = []
    for question in questions:
        retrieval = service.retrieve(
            question.question,
            filters=question.filters,
        )
        package = builder.build(retrieval.query, retrieval.results)
        assessment = guard.assess(package)
        current_ids = [item.chunk_id for item in retrieval.results]
        baseline_ids = [
            item["chunk_id"]
            for item in baseline[question.question_id]["layers"]["hybrid_reranked"]["results"]
        ]
        cases.append(
            {
                "question_id": question.question_id,
                "question_type": question.question_type,
                "ground_truth_status": question.ground_truth_status,
                "expected_evidence_state": question.expected_evidence_state,
                "assessment": assessment.model_dump(mode="json"),
                "retrieval_result_ids": current_ids,
                "baseline_result_ids": baseline_ids,
                "retrieval_order_unchanged": current_ids == baseline_ids,
                "retrieval_ms": retrieval.timings.get("total_ms"),
                "guarded_total_without_generation_ms": (
                    round(float(retrieval.timings["total_ms"]) + assessment.assessment_ms, 3)
                    if retrieval.timings.get("total_ms") is not None else None
                ),
                "provider_called": False,
            }
        )

    positive_verified = [
        case
        for case, question in zip(cases, questions)
        if question.ground_truth_status == "VERIFIED" and question.expected_chunk_ids
    ]
    negatives = [
        case
        for case, question in zip(cases, questions)
        if question.expected_evidence_state == "NO_VERIFIED_EVIDENCE"
    ]
    states = Counter(case["assessment"]["state"] for case in cases)
    guard_latencies = [float(case["assessment"]["assessment_ms"]) for case in cases]
    avoided = sum(
        case["assessment"]["state"] in {"INSUFFICIENT", "OUT_OF_DOMAIN"}
        for case in cases
    )
    report = {
        "stage": "4.6",
        "generated_at": utc_now(),
        "dataset": {
            "path": str(questions_path),
            "sha256": sha256_file(questions_path),
            "questions": len(questions),
        },
        "stage_4_4_baseline": {
            "path": str(baseline_path),
            "sha256": sha256_file(baseline_path),
            "retrieval_orders_unchanged": sum(case["retrieval_order_unchanged"] for case in cases),
            "evaluated": len(cases),
            "baseline_files_modified": False,
        },
        "calibration": {
            "version": CALIBRATION_VERSION,
            "provisional_reranker_support_floor": MIN_RERANKER_SUPPORT_SCORE,
            "verified_positive_top_score_min": min(positive_top_scores) if positive_top_scores else None,
            "candidate_returning_negative_top_score_max": max(negative_top_scores) if negative_top_scores else None,
            "verified_positive_questions": len(positive_top_scores),
            "candidate_returning_negative_questions": len(negative_top_scores),
            "limitation": (
                "The dataset has only one negative question that returns candidates; "
                "the floor is provisional and is never used without other signals."
            ),
        },
        "assessment": {
            "states": dict(sorted(states.items())),
            "verified_positive_preserved": sum(
                case["assessment"]["state"] in {"SUFFICIENT", "PARTIAL"}
                for case in positive_verified
            ),
            "verified_positive_evaluated": len(positive_verified),
            "negative_guarded": sum(
                case["assessment"]["state"] in {"INSUFFICIENT", "OUT_OF_DOMAIN"}
                for case in negatives
            ),
            "negative_evaluated": len(negatives),
            "gemini_calls_avoided": avoided,
            "provider_calls_made": 0,
        },
        "latency_ms": {
            "guard": _latency(guard_latencies),
            "retrieval": _latency(
                [float(case["retrieval_ms"]) for case in cases if case["retrieval_ms"] is not None]
            ),
            "guarded_total_without_generation": _latency(
                [
                    float(case["guarded_total_without_generation_ms"])
                    for case in cases
                    if case["guarded_total_without_generation_ms"] is not None
                ]
            ),
            "generation": None,
            "note": "Generation was not run because no configured provider was available.",
        },
        "cases": cases,
        "no_overall_rag_accuracy": True,
    }
    destination = output_path or evaluation_dir / REPORT_FILE
    atomic_json(destination, report)
    return report
