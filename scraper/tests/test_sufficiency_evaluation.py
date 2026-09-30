"""Stage 4.6 evaluation report and CLI contract tests."""

from __future__ import annotations

import json
from pathlib import Path

from scraper.cli import parser
from scraper.sufficiency_evaluation import _latency
from scraper.utils import sha256_file


REPORT = Path("data/evaluation/stage_4_6_evaluation_report.json")


def test_sufficiency_cli_is_exposed_without_answer_provider_flags():
    args = parser().parse_args(["sufficiency-eval", "--json"])
    assert args.command == "sufficiency-eval"
    assert args.json


def test_guard_latency_summary_is_deterministic():
    assert _latency([0.3, 0.1, 0.2]) == {
        "count": 3,
        "mean": 0.2,
        "median": 0.2,
        "max": 0.3,
    }
    assert _latency([])["mean"] is None


def test_generated_stage_46_report_is_separate_and_traceable():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["stage"] == "4.6"
    assert report["dataset"]["sha256"] == sha256_file(Path("data/evaluation/questions.jsonl"))
    assert report["stage_4_4_baseline"]["baseline_files_modified"] is False
    assert report["stage_4_4_baseline"]["retrieval_orders_unchanged"] == 27
    assert report["no_overall_rag_accuracy"] is True


def test_generated_report_records_negative_guard_and_verified_preservation():
    report = json.loads(REPORT.read_text(encoding="utf-8"))
    assert report["assessment"]["verified_positive_preserved"] == 22
    assert report["assessment"]["verified_positive_evaluated"] == 22
    assert report["assessment"]["negative_guarded"] == 2
    cases = {item["question_id"]: item for item in report["cases"]}
    assert cases["q024"]["assessment"]["state"] == "OUT_OF_DOMAIN"
    assert cases["q025"]["assessment"]["state"] == "INSUFFICIENT"
