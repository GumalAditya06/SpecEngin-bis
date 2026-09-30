# Specengine Stage 4.4 evaluation

This directory is a versioned, human-reviewable benchmark for the frozen BIS
retrieval and grounded-answer pipeline. It measures retrieval separately from
answer generation and deliberately reports no single “RAG accuracy” number.

## Dataset

`questions.jsonl` uses dataset version `1.0.0`. `null` means that ground truth
is unknown; an empty list means it is verified that no evidence is expected.
Only `VERIFIED` questions contribute to a metric, and only when the relevant
expected field is populated. `UNVERIFIED` questions are retained for inspection
without being scored.

The 12 categories are exact standard lookup, definition, requirement, testing,
sampling, licensing/certification, clause-specific, table, annex,
cross-document, negative/insufficient-evidence, and ambiguous questions.

## Run

```sh
.venv/bin/python -m scraper evaluate --retrieval-only
.venv/bin/python -m scraper evaluate --category table
.venv/bin/python -m scraper evaluate --question-id q004
.venv/bin/python -m scraper evaluate --limit 5
.venv/bin/python -m scraper evaluate --answers
```

Retrieval-only evaluation never needs an API key. Answer evaluation uses the
existing provider selected by `BIS_LLM_PROVIDER`, `BIS_LLM_MODEL`, and
`BIS_LLM_API_KEY`; missing configuration fails explicitly and never selects a
fallback provider.

## Outputs

- `retrieval_results.jsonl`: per-question BM25, BGE, RRF, and final frozen
  reranked results, layer timings, valid metrics, and failures.
- `answer_results.jsonl`: validated grounded answers, citations, usage,
  latency, citation checks, and a manual groundedness field.
- `evaluation_report.json`: aggregate metrics, latency, exact-identifier checks,
  input/configuration hashes, and counts.
- `failure_analysis.json`: failures grouped by the Stage 4.4 taxonomy.
- `stage_4_6_evaluation_report.json`: separate post-retrieval sufficiency
  decisions, guard latency, negative-query handling, verified-question
  preservation, and a result-order comparison against the unchanged Stage 4.4
  baseline.

The final retriever returns at most five results by frozen policy, so its
Recall@10 is explicitly unavailable. BM25, semantic, and RRF Recall@10 remain
measurable from their unchanged candidate rankings.

## Manual groundedness review

The schema leaves `groundedness_review` null until a human reviews the answer.
Set it to `PASS`, `FAIL`, or `UNCERTAIN` after checking whether the cited text
supports the answer, whether unsupported facts or contradictions were added,
whether it overgeneralizes, and whether uncertainty is communicated correctly.
Schema/citation validation is not treated as semantic entailment.

## Reproducibility

The report records the dataset, raw corpus tree, chunks, embeddings, and vector
configuration hashes; pinned BGE and reranker revisions; BM25/RRF values; model
selection; runtime versions; timestamp; and cold/warm latency separately.
Latency and tiny floating-point differences may vary by CPU, BLAS, PyTorch, and
filesystem cache. Stable IDs and ranking tie-breaks remain deterministic.
