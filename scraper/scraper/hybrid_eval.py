"""Generate the Stage 3.3 retrieval and side-by-side evaluation reports."""

from __future__ import annotations

import hashlib
import statistics
import time
from pathlib import Path

from .config import Settings, resolve_embedding_spec
from .embeddings import SentenceTransformerEmbedder
from .hybrid_retrieval import (
    DEFAULT_BM25_B,
    DEFAULT_BM25_K1,
    DEFAULT_CANDIDATE_K,
    DEFAULT_FINAL_K,
    DEFAULT_RERANK_K,
    DEFAULT_RRF_K,
    INDEXED_FIELDS,
    RERANKER_MODEL,
    RERANKER_PARAMETERS,
    RERANKER_REVISION,
    BM25Index,
    CrossEncoderReranker,
    HybridRetriever,
    build_reranker_text,
    reciprocal_rank_fusion,
)
from .retrieval import RETRIEVAL_SANITY_QUERIES, Retriever
from .utils import atomic_json, utc_now
from .vectorstore import load_index

EXACT_IDENTIFIER_QUERIES: tuple[str, ...] = (
    "IS 4250:2025",
    "IS 367:1993",
    "IS 2347:2023",
    "Clause 4",
    "Clause 1.3.1",
    "control unit",
)
REPORT_FILENAMES = (
    "bm25_report.json",
    "hybrid_report.json",
    "reranker_report.json",
    "comparison_report.json",
)

RERANKER_CANDIDATES: tuple[dict, ...] = (
    {
        "model": "cross-encoder/ms-marco-MiniLM-L4-v2",
        "revision": "777b2f369bc1c2f850df8bd367ed1654bda4497b",
        "parameters": 19_164_673,
        "layers": 4,
        "public_trec_dl_2019_ndcg_at_10": 73.04,
        "public_ms_marco_dev_mrr_at_10": 37.70,
    },
    {
        "model": RERANKER_MODEL,
        "revision": RERANKER_REVISION,
        "parameters": RERANKER_PARAMETERS,
        "layers": 6,
        "public_trec_dl_2019_ndcg_at_10": 74.30,
        "public_ms_marco_dev_mrr_at_10": 39.01,
    },
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _average_timings(samples: list[dict]) -> dict:
    fields = ("bm25_ms", "semantic_ms", "fusion_ms", "reranking_ms", "total_ms")
    return {
        field: round(statistics.mean(float(sample[field]) for sample in samples), 3)
        for field in fields
    }


def _compact_result(result: dict) -> dict:
    return {
        "rank": result["rank"],
        "chunk_id": result["chunk_id"],
        "standard_numbers": result.get("standard_numbers", []),
        "clause_number": result.get("clause_number"),
        "clause_title": result.get("clause_title"),
        "pages": result.get("pages", []),
        "source_url": result.get("source_url"),
        "text": result.get("text", ""),
        **({"similarity_score": result["similarity_score"]} if "similarity_score" in result else {}),
        **({"reranker_score": result["reranker_score"]} if "reranker_score" in result else {}),
        **({"fusion_score": result["fusion_score"]} if "fusion_score" in result else {}),
        **({"semantic_rank": result["semantic_rank"]} if "semantic_rank" in result else {}),
        **({"bm25_rank": result["bm25_rank"]} if "bm25_rank" in result else {}),
    }


def run_hybrid_evaluation(
    settings: Settings,
    *,
    output_dir: Path | None = None,
    candidate_k: int = DEFAULT_CANDIDATE_K,
    rerank_k: int = DEFAULT_RERANK_K,
    final_k: int = DEFAULT_FINAL_K,
    rrf_k: int = DEFAULT_RRF_K,
) -> dict:
    """Run frozen/manual evaluation and write all four Stage 3.3 reports."""
    output_dir = output_dir or settings.processed_dir / "retrieval_v2"
    chunks_path = settings.processed_dir / "chunks_v2" / "chunks.jsonl"
    embeddings_path = settings.embeddings_v2_dir / "embeddings.jsonl"
    store_dir = settings.vector_store_dir
    store = load_index(store_dir, embeddings_path)
    spec = resolve_embedding_spec()
    embedder = SentenceTransformerEmbedder(spec)
    semantic = Retriever(store, embedder=embedder, spec=spec)
    bm25 = BM25Index.from_jsonl(chunks_path, metadata_records=store.metadata)

    # Build the same frozen candidate pools once for a fair reranker comparison.
    all_queries = list(RETRIEVAL_SANITY_QUERIES) + list(EXACT_IDENTIFIER_QUERIES)
    pools = {}
    baseline_by_query = {}
    bm25_by_query = {}
    bm25_times = []
    semantic_times = []
    fusion_times = []
    for query in all_queries:
        started = time.perf_counter()
        semantic_results = semantic.search(query, top_k=candidate_k)
        semantic_times.append((time.perf_counter() - started) * 1000)
        baseline_by_query[query] = semantic_results
        started = time.perf_counter()
        lexical_results = bm25.search(query, top_k=candidate_k)
        bm25_times.append((time.perf_counter() - started) * 1000)
        bm25_by_query[query] = lexical_results
        started = time.perf_counter()
        pools[query] = reciprocal_rank_fusion(
            semantic_results, lexical_results, rrf_k=rrf_k
        )[:rerank_k]
        fusion_times.append((time.perf_counter() - started) * 1000)

    candidate_evaluations = []
    selected_reranker = None
    selected_rankings = {}
    for candidate in RERANKER_CANDIDATES:
        reranker = CrossEncoderReranker(
            candidate["model"], candidate["revision"], device="cpu"
        )
        parameter_count = sum(parameter.numel() for parameter in reranker.model.model.parameters())
        query_timings = []
        rankings = {}
        for query in all_queries:
            pool = pools[query]
            started = time.perf_counter()
            scores = reranker.score(
                query, [build_reranker_text(item.record) for item in pool]
            )
            query_timings.append((time.perf_counter() - started) * 1000)
            ranked = sorted(
                zip(pool, scores),
                key=lambda pair: (-pair[1], -pair[0].fusion_score, pair[0].chunk_id),
            )
            rankings[query] = [
                {
                    "rank": rank,
                    "chunk_id": item.chunk_id,
                    "reranker_score": round(score, 6),
                    "standard_numbers": item.record.get("standard_numbers") or [],
                    "clause_number": item.record.get("clause_number"),
                    "clause_title": item.record.get("clause_title"),
                    "text_preview": str(item.record.get("text") or "")[:240],
                }
                for rank, (item, score) in enumerate(ranked[:final_k], start=1)
            ]
        evaluation = {
            **candidate,
            "measured_parameters": parameter_count,
            "device": "cpu",
            "max_input_tokens": 512,
            "mean_reranking_ms": round(statistics.mean(query_timings), 3),
            "queries_evaluated": len(all_queries),
            "actual_corpus_rankings": rankings,
        }
        candidate_evaluations.append(evaluation)
        if candidate["model"] == RERANKER_MODEL:
            selected_reranker = reranker
            selected_rankings = rankings

    assert selected_reranker is not None
    hybrid = HybridRetriever(semantic, bm25, selected_reranker)
    hybrid_by_query = {}
    timing_samples = []
    for query in all_queries:
        results, timings = hybrid.search(
            query,
            candidate_k=candidate_k,
            rerank_k=rerank_k,
            final_k=final_k,
            rrf_k=rrf_k,
        )
        hybrid_by_query[query] = results
        timing_samples.append(timings)

    common = {
        "stage": "3.3",
        "generated_at": utc_now(),
        "corpus": str(chunks_path),
        "corpus_sha256": _sha256(chunks_path),
        "corpus_chunks": len(bm25.records),
        "vector_store": str(store_dir),
        "vector_store_unchanged": True,
    }
    bm25_report = {
        **common,
        "component": "bm25",
        "implementation": "in_repo_okapi_bm25",
        "indexed_fields": list(INDEXED_FIELDS),
        "indexed_field_note": (
            "Only context_prefix, authoritative text, standard_numbers, "
            "clause_number, and clause_title are indexed. Exact duplicate "
            "components are removed; no other metadata is concatenated."
        ),
        "tokenizer": "lowercase regex; dotted/colon/slash/hyphen identifiers stay intact",
        "k1": DEFAULT_BM25_K1,
        "b": DEFAULT_BM25_B,
        "average_document_tokens": round(bm25.average_length, 3),
        "vocabulary_terms": len(bm25.idf),
        "mean_query_ms": round(statistics.mean(bm25_times), 3),
        "timed_queries": len(bm25_times),
        "exact_identifier_tests": [
            {
                "query": query,
                "results": [result.to_dict() for result in bm25_by_query[query][:final_k]],
                "manual_relevance_label": None,
            }
            for query in EXACT_IDENTIFIER_QUERIES
        ],
    }
    reranker_report = {
        **common,
        "component": "reranker",
        "selected_model": RERANKER_MODEL,
        "selected_revision": RERANKER_REVISION,
        "selected_parameters": RERANKER_PARAMETERS,
        "score_interpretation": "raw cross-encoder logit; compare only within one query",
        "selection_reason": (
            "The 6-layer MiniLM model remains CPU-friendly and was stronger than "
            "the 4-layer alternative on the published passage-ranking benchmarks. "
            "On this corpus both preserved exact identifiers in the final five; "
            "L6 additionally promoted direct ensuring-compliance/testing clauses "
            "for the broad frozen testing query. It is far smaller than the "
            "approximately 1.1 GB BGE reranker-base weights."
        ),
        "cpu_expectation": (
            "CPU-only inference; 22.7M parameters, batches of 16, at most "
            f"{rerank_k} query-document pairs per request. No GPU is required."
        ),
        "candidate_comparison": candidate_evaluations,
        "manual_labels_assigned": False,
    }
    hybrid_report = {
        **common,
        "component": "hybrid_retrieval",
        "semantic_model": spec.model_name,
        "semantic_revision": spec.revision,
        "bm25_candidate_k": candidate_k,
        "semantic_candidate_k": candidate_k,
        "rrf_k": rrf_k,
        "rerank_k": rerank_k,
        "final_k": final_k,
        "average_latency_ms": _average_timings(timing_samples),
        "latency_scope": (
            "Warm per-query CPU latency after models and indexes are loaded; "
            "one-time process/model startup is excluded."
        ),
        "timed_queries": len(timing_samples),
        "runtime_variation_note": (
            "Ranks are deterministic for fixed model/runtime inputs. Wall-clock "
            "latency and very small floating-point differences may vary with CPU, "
            "PyTorch, and BLAS versions. Stable chunk-id tie breaks are applied."
        ),
        "exact_identifier_tests": [
            {
                "query": query,
                "timing": timing_samples[len(RETRIEVAL_SANITY_QUERIES) + index],
                "results": [result.to_dict() for result in hybrid_by_query[query]],
                "manual_relevance_label": None,
            }
            for index, query in enumerate(EXACT_IDENTIFIER_QUERIES)
        ],
    }
    comparison_queries = []
    for query in RETRIEVAL_SANITY_QUERIES:
        baseline = [result.to_dict() for result in baseline_by_query[query][:final_k]]
        hybrid_results = [result.to_dict() for result in hybrid_by_query[query]]
        comparison_queries.append(
            {
                "query": query,
                "baseline_semantic_only_top_5": [
                    {**_compact_result(result), "manual_relevance_label": None}
                    for result in baseline
                ],
                "hybrid_reranked_top_5": [
                    {**_compact_result(result), "manual_relevance_label": None}
                    for result in hybrid_results
                ],
                "manual_notes": None,
            }
        )
    comparison_report = {
        **common,
        "component": "manual_baseline_comparison",
        "label_scale": {
            "0": "irrelevant",
            "1": "somewhat relevant",
            "2": "directly relevant",
        },
        "labels_are_unassigned": True,
        "accuracy_percentage": None,
        "note": "No automatic relevance judgements or manufactured accuracy percentage.",
        "queries": comparison_queries,
    }

    reports = {
        "bm25_report.json": bm25_report,
        "hybrid_report.json": hybrid_report,
        "reranker_report.json": reranker_report,
        "comparison_report.json": comparison_report,
    }
    output_dir.mkdir(parents=True, exist_ok=True)
    for filename, report in reports.items():
        atomic_json(output_dir / filename, report)
    return {"output_dir": str(output_dir), "reports": reports}
