"""Stage 4.4 evaluation contracts, metrics, recording, and CLI tests."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from scraper.cli import main, parser
from scraper.evaluation_models import (
    AnswerCaseResult,
    CitationEvaluation,
    EvaluationQuestion,
    LayerEvaluation,
    LayerResult,
    RetrievalCaseResult,
)
from scraper.rag_evaluation import (
    EvaluationError,
    _retrieval_failures,
    _tree_hash,
    _write_jsonl,
    evaluate_citations,
    load_questions,
    recall_at_k,
    reciprocal_rank,
    run_evaluation,
    select_questions,
)


DATASET = Path("data/evaluation/questions.jsonl")


def question(**overrides) -> EvaluationQuestion:
    values = {
        "question_id": "q900",
        "question": "What is required?",
        "question_type": "requirement",
        "difficulty": "easy",
        "ground_truth_status": "VERIFIED",
        "expected_documents": ["doc-a"],
        "expected_standard_numbers": ["IS 1:2020"],
        "expected_clause_numbers": ["4"],
        "expected_chunk_ids": ["chunk-a"],
        "expected_pages": [2],
        "expected_evidence": None,
        "expected_evidence_state": "VERIFIED_EVIDENCE",
        "expected_chunks_exhaustive": True,
        "filters": None,
        "notes": None,
    }
    values.update(overrides)
    return EvaluationQuestion(**values)


def layer(chunk_id="chunk-a", *, standard="IS 1:2020", clause="4", document="doc-a") -> LayerResult:
    return LayerResult(
        rank=1,
        chunk_id=chunk_id,
        document_id=document,
        standard_numbers=[standard],
        clause_number=clause,
        annex_identifier=None,
        table_identifier=None,
        pages=[2],
        score=1.0,
    )


def test_versioned_dataset_schema_and_balanced_categories():
    questions = load_questions(DATASET)
    assert len(questions) == 27
    assert {item.dataset_version for item in questions} == {"1.0.0"}
    assert len({item.question_type for item in questions}) == 12
    assert sum(item.ground_truth_status == "VERIFIED" for item in questions) == 24
    assert sum(item.ground_truth_status == "UNVERIFIED" for item in questions) == 3


def test_question_loading_is_deterministic():
    first = [item.model_dump() for item in load_questions(DATASET)]
    second = [item.model_dump() for item in load_questions(DATASET)]
    assert first == second


def test_unknown_ground_truth_stays_explicitly_unverified():
    value = question(
        ground_truth_status="UNVERIFIED",
        expected_documents=None,
        expected_standard_numbers=None,
        expected_clause_numbers=None,
        expected_chunk_ids=None,
        expected_pages=None,
        expected_evidence_state=None,
    )
    assert value.expected_chunk_ids is None
    with pytest.raises(ValidationError, match="must not claim"):
        question(ground_truth_status="UNVERIFIED")


@pytest.mark.parametrize("k,expected", [(1, 0.5), (3, 1.0), (5, 1.0), (10, 1.0)])
def test_recall_at_k(k, expected):
    assert recall_at_k(["a", "x", "b"], ["a", "b"], k) == expected


def test_mrr_uses_first_relevant_rank():
    assert reciprocal_rank(["x", "a", "b"], ["a", "b"]) == 0.5
    assert reciprocal_rank(["x"], ["a"]) == 0.0


def test_metrics_skip_missing_or_known_empty_ground_truth():
    assert recall_at_k(["a"], None, 5) is None
    assert recall_at_k(["a"], [], 5) is None
    assert reciprocal_rank(["a"], None) is None


def test_duplicate_question_ids_are_rejected(tmp_path):
    row = question().model_dump(mode="json")
    path = tmp_path / "questions.jsonl"
    path.write_text(json.dumps(row) + "\n" + json.dumps(row) + "\n")
    with pytest.raises(EvaluationError, match="duplicate question_id"):
        load_questions(path)


def test_invalid_expected_chunk_id_is_rejected(tmp_path):
    path = tmp_path / "questions.jsonl"
    path.write_text(json.dumps(question().model_dump(mode="json")) + "\n")
    with pytest.raises(EvaluationError, match="unknown chunk IDs"):
        load_questions(path, valid_chunk_ids={"different"})


def test_retrieval_result_recording_round_trip(tmp_path):
    case = RetrievalCaseResult(
        question_id="q900",
        question="What is required?",
        question_type="requirement",
        ground_truth_status="VERIFIED",
        filters=None,
        layers={"hybrid_reranked": LayerEvaluation(latency_ms=1.2, results=[layer()])},
        metrics={"recall_at_1": 1.0},
        failure_categories=[],
    )
    path = tmp_path / "retrieval.jsonl"
    _write_jsonl(path, [case.model_dump(mode="json")])
    assert json.loads(path.read_text())["layers"]["hybrid_reranked"]["results"][0]["chunk_id"] == "chunk-a"


def test_answer_result_recording_round_trip(tmp_path):
    citation = CitationEvaluation(
        citation_exists_when_required=True,
        ids_correspond_to_evidence=True,
        metadata_matches_evidence=True,
        unsupported_citation_ids=[],
        unrelated_evidence=False,
        standard_number_correct=True,
        clause_number_correct=True,
        page_correct=True,
        source_document_correct=True,
        evidence_excerpt_matches=True,
        passed=True,
    )
    case = AnswerCaseResult(
        question_id="q900", question="What is required?", ground_truth_status="VERIFIED",
        status="COMPLETED", answer="Requirement [E1]", evidence_state="VERIFIED_EVIDENCE",
        citations=[], evidence=[], limitations=[], citation_evaluation=citation,
        failure_categories=[], latency_ms={"total": 2.0}, usage=None,
        provider="fake", model="fake",
    )
    path = tmp_path / "answers.jsonl"
    _write_jsonl(path, [case.model_dump(mode="json")])
    assert json.loads(path.read_text())["citation_evaluation"]["passed"] is True


def _answer(citations=True, state="VERIFIED_EVIDENCE"):
    evidence = SimpleNamespace(
        evidence_id="E1", chunk_id="chunk-a", standard="IS 1:2020",
        clause_number="4", clause_title="Requirements", document_id="doc-a",
        source_id=None, title="Document", version=None, page=2,
        url="https://example.invalid/a.pdf", authoritative_text="Exact evidence",
    )
    citation = SimpleNamespace(
        evidence_id="E1", standard_number="IS 1:2020", clause_number="4",
        clause_title="Requirements", document_id="doc-a", source_id=None,
        title="Document", version=None, page=2, url="https://example.invalid/a.pdf",
    )
    return SimpleNamespace(
        evidence_state=state,
        evidence=[] if state == "NO_VERIFIED_EVIDENCE" else [evidence],
        citations=[citation] if citations else [],
    )


def test_citation_evaluation_matches_supplied_evidence():
    result = evaluate_citations(question(), _answer())
    assert result.passed
    assert result.unsupported_citation_ids == []
    assert result.metadata_matches_evidence


def test_no_evidence_citation_evaluation_requires_no_citations():
    negative = question(
        question_type="negative_insufficient_evidence",
        expected_documents=[], expected_standard_numbers=[], expected_clause_numbers=[],
        expected_chunk_ids=[], expected_pages=[], expected_evidence=[],
        expected_evidence_state="NO_VERIFIED_EVIDENCE",
    )
    result = evaluate_citations(negative, _answer(citations=False, state="NO_VERIFIED_EVIDENCE"))
    assert result.passed and result.citation_exists_when_required


@pytest.mark.parametrize(
    "kind,actual,expected",
    [
        ("requirement", layer(chunk_id="other", standard="IS 2:2020"), "WRONG_STANDARD"),
        ("table", layer(chunk_id="other"), "WRONG_TABLE"),
        ("annex", layer(chunk_id="other"), "WRONG_ANNEX"),
    ],
)
def test_failure_classification(kind, actual, expected):
    value = question(question_type=kind)
    assert _retrieval_failures(value, [actual], []) == [expected]


def test_reranking_failure_classification():
    value = question()
    assert _retrieval_failures(value, [layer(chunk_id="other")], [layer()]) == ["RERANKING_ERROR"]


def test_dataset_and_tree_hashes_are_reproducible(tmp_path):
    (tmp_path / "b").write_text("two")
    (tmp_path / "a").write_text("one")
    assert _tree_hash(tmp_path) == _tree_hash(tmp_path)


def test_question_filters_and_limit_preserve_dataset_order():
    values = load_questions(DATASET)
    selected = select_questions(values, categories=["table"], limit=2)
    assert [item.question_id for item in selected] == ["q016", "q017"]
    assert select_questions(values, question_ids=["q004"])[0].question_id == "q004"


def test_cli_exposes_evaluation_selection_flags():
    args = parser().parse_args(["evaluate", "--retrieval-only", "--question-id", "q004", "--category", "definition", "--limit", "1"])
    assert args.command == "evaluate" and args.retrieval_only
    assert args.question_id == ["q004"] and args.category == ["definition"] and args.limit == 1


def test_answer_cli_fails_clearly_without_provider(monkeypatch, tmp_path, capsys):
    for name in ("BIS_LLM_PROVIDER", "BIS_LLM_API_KEY", "BIS_LLM_MODEL"):
        monkeypatch.delenv(name, raising=False)
    assert main(["evaluate", "--answers", "--data-dir", str(tmp_path)]) == 2
    assert "BIS_LLM_PROVIDER is required" in capsys.readouterr().out


def test_custom_output_directory_does_not_relocate_default_dataset(monkeypatch, tmp_path):
    data_dir = tmp_path / "data"
    questions_dir = data_dir / "evaluation"
    chunks_dir = data_dir / "processed" / "chunks_v2"
    questions_dir.mkdir(parents=True)
    chunks_dir.mkdir(parents=True)
    (questions_dir / "questions.jsonl").write_text(
        json.dumps(question().model_dump(mode="json")) + "\n"
    )
    (chunks_dir / "chunks.jsonl").write_text(json.dumps({"chunk_id": "chunk-a"}) + "\n")

    class StopAfterLoading(RuntimeError):
        pass

    def stop_before_models(_settings):
        raise StopAfterLoading

    monkeypatch.setattr("scraper.rag_evaluation.RetrievalService.from_settings", stop_before_models)
    with pytest.raises(StopAfterLoading):
        run_evaluation(
            SimpleNamespace(data_dir=data_dir, processed_dir=data_dir / "processed"),
            output_dir=tmp_path / "other-output",
        )
