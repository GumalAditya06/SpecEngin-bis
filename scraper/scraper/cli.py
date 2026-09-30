from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from .config import PRODUCTS, Settings
from .coverage import build_coverage_report
from .cleaning import build_metadata_consistency_report, run_cleaning
from .chunking import ChunkConfig, resolve_embedding_max_tokens, run_chunking
from .chunking_v2 import run_chunking_v2
from .config import EMBEDDING_MODELS
from .discovery import Discoverer
from .downloader import Downloader
from .http import PoliteClient
from .lims import LIMSCollector
from .manifest import CorpusStore
from .embeddings import run_embeddings
from .extraction import run_extraction
from .structure import run_structure
from .validation import audit_corpus


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="Build the traceable Specengine BIS evidence corpus")
    root.add_argument("command", choices=("discover", "download", "run", "status", "retry-failed", "validate", "coverage", "laboratories", "extract", "clean", "metadata-check", "structure", "chunk", "chunk-v2", "embed", "index", "search", "retrieve-eval", "hybrid-search", "hybrid-eval", "evaluate", "sufficiency-eval"))
    root.add_argument("text", nargs="?", help="query text, for the search command")
    selection = root.add_mutually_exclusive_group()
    selection.add_argument("--product", choices=sorted(PRODUCTS))
    selection.add_argument("--all", action="store_true", help="process all configured products")
    root.add_argument("--data-dir", type=Path, default=Path("data"))
    root.add_argument("--delay", type=float, default=1.0, help="minimum seconds between requests to one host")
    root.add_argument("--timeout", type=float, default=30.0)
    root.add_argument("--max-retries", type=int, default=3)
    root.add_argument("--max-pages-per-domain", type=int, default=30)
    root.add_argument("--max-depth", type=int, default=1)
    root.add_argument("--max-documents-per-product", type=int, default=25)
    root.add_argument("--target-tokens", type=int, default=700)
    root.add_argument("--soft-max-tokens", type=int, default=1000)
    root.add_argument("--hard-max-tokens", type=int, default=1200)
    root.add_argument("--overlap-tokens", type=int, default=75)
    root.add_argument("--tokenizer", choices=("regex_v1", "bge"), default="regex_v1",
                      help="token counter for chunking; bge uses the embedding model's own tokenizer")
    root.add_argument("--embedding-max-tokens", type=int,
                      help="safe ceiling for the whole embedding input, prefix included "
                           "(default: 450, or $EMBEDDING_MAX_TOKENS)")
    root.add_argument("--chunks-file", type=Path, help="chunks.jsonl to embed (default: the Stage 2.5 corpus)")
    root.add_argument("--output-dir", type=Path, help="directory for generated artefacts")
    root.add_argument(
        "--embedding-model",
        choices=sorted(EMBEDDING_MODELS),
        help="registered Stage 3.1 embedding model (default: bge-base-en-v1.5, or $EMBEDDING_MODEL)",
    )
    root.add_argument("--embedding-revision", help="pin a model revision; defaults to the registered pin")
    root.add_argument("--batch-size", type=int, help="embedding batch size (default: 32, or $EMBEDDING_BATCH_SIZE)")
    root.add_argument("--device", help="torch device for inference (default: cpu, or $EMBEDDING_DEVICE)")
    root.add_argument("--top-k", type=int, default=5, help="sanity-test results per query")
    root.add_argument("--candidate-k", type=int, default=20,
                      help="semantic and BM25 candidates per source (default: 20)")
    root.add_argument("--rerank-k", type=int, default=40,
                      help="maximum unique fused candidates to rerank (default: 40)")
    root.add_argument("--rrf-k", type=int, default=60,
                      help="reciprocal-rank-fusion constant (default: 60)")
    root.add_argument("--embeddings-file", type=Path,
                      help="embeddings.jsonl to index (default: the Stage 3.1A corpus in embeddings_v2/)")
    root.add_argument("--store-dir", type=Path, help="vector index directory")
    root.add_argument("--json", action="store_true", help="print machine-readable JSON")
    evaluation_mode = root.add_mutually_exclusive_group()
    evaluation_mode.add_argument("--retrieval-only", action="store_true",
                                 help="run Stage 4.4 retrieval evaluation without an LLM (default)")
    evaluation_mode.add_argument("--answers", action="store_true",
                                 help="also run grounded-answer evaluation; requires configured provider")
    root.add_argument("--question-id", action="append",
                      help="evaluate one question ID; repeat to select multiple")
    root.add_argument("--category", action="append",
                      help="evaluate one question category; repeat to select multiple")
    root.add_argument("--limit", type=int, help="evaluate only the first N selected questions")
    root.add_argument("--questions-file", type=Path,
                      help="versioned questions.jsonl (default: data/evaluation/questions.jsonl)")
    for name, help_text in (
        ("standard", "restrict results to a standard, e.g. IS 4250:2025"),
        ("document", "restrict results to a document_id"),
        ("clause", "restrict results to a clause_number"),
        ("annex", "restrict results to an annex_identifier"),
        ("type", "restrict results to a document_type"),
        ("product", "restrict results to a product (kettle, mixer, pressure_cooker)"),
    ):
        root.add_argument(f"--filter-{name}", help=help_text)
    return root


def _selected(args) -> list[str]:
    return [args.product] if args.product else list(PRODUCTS)


def _settings(args) -> Settings:
    return Settings(
        data_dir=args.data_dir, request_delay=max(0, args.delay), timeout=args.timeout,
        max_retries=max(0, args.max_retries), max_pages_per_domain=max(1, args.max_pages_per_domain),
        max_depth=max(0, args.max_depth), max_documents_per_product=max(1, args.max_documents_per_product),
    )


def print_report(store: CorpusStore, stats: Counter | None = None, downloaded_files: list[str] | None = None) -> None:
    stats = stats or Counter()
    labels = [("mixer", "Mixer"), ("kettle", "Electric kettle"), ("pressure_cooker", "Pressure cooker"), ("common", "Common")]
    print("\nSPECENGINE BIS CORPUS COLLECTION\n")
    print("Products:")
    for product_id, label in labels:
        count = sum(r.get("status") == "downloaded" and r.get("product_id") == product_id for r in store.records)
        print(f"    {label + ':':20} {count} documents")
    total_downloaded = sum(r.get("status") == "downloaded" for r in store.records)
    total_duplicates = sum(r.get("status") == "duplicate" for r in store.records)
    total_failed = sum(r.get("status") == "failed" for r in store.records)
    unavailable = sum(r.get("status") == "discovered_not_downloaded" for r in store.records)
    low = sum(r.get("relevance") in {"LOW", "REJECTED"} or r.get("status") == "rejected" for r in store.records)
    laboratories = CorpusStore._read_json(store.settings.metadata_dir / "laboratories.json", [])
    laboratory_count = sum(r.get("status") == "collected" for r in laboratories)
    print(f"\nDownloaded:\n    {total_downloaded} ({stats.get('downloaded', 0)} this run)")
    print(f"\nSkipped:\n    {stats.get('skipped', 0)}")
    print(f"\nDuplicates:\n    {total_duplicates}")
    print(f"\nFailed:\n    {total_failed}")
    print(f"\nDiscovered but not downloaded:\n    {unavailable + low}")
    print(f"\nLaboratory records:\n    {laboratory_count}")
    files = downloaded_files if downloaded_files is not None else [r["local_path"] for r in store.records if r.get("status") == "downloaded" and r.get("local_path")]
    print("\nDownloaded files:")
    if files:
        for path in files:
            record = next((r for r in store.records if r.get("local_path") == path), {})
            print(f"    {path}\n        source: {record.get('source_url', 'unknown')}")
    else:
        print("    (none this run)")


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    settings = _settings(args)
    store = CorpusStore(settings)
    if args.command == "sufficiency-eval":
        from .sufficiency_evaluation import run_sufficiency_evaluation

        report = run_sufficiency_evaluation(
            settings,
            output_path=(args.output_dir / "stage_4_6_evaluation_report.json")
            if args.output_dir else None,
        )
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            assessment = report["assessment"]
            print("\nSTAGE 4.6 EVIDENCE SUFFICIENCY EVALUATION\n")
            print(f"Questions:                    {report['dataset']['questions']}")
            print(f"Verified positives preserved: {assessment['verified_positive_preserved']}/{assessment['verified_positive_evaluated']}")
            print(f"Negative cases guarded:       {assessment['negative_guarded']}/{assessment['negative_evaluated']}")
            print(f"Gemini calls avoided:         {assessment['gemini_calls_avoided']}")
            print("Overall RAG accuracy is intentionally not reported.")
        return 0
    if args.command == "evaluate":
        from .llm_provider import ProviderConfigurationError, provider_from_env
        from .rag_evaluation import EvaluationError, run_evaluation

        try:
            provider = provider_from_env() if args.answers else None
            report = run_evaluation(
                settings,
                output_dir=args.output_dir,
                questions_path=args.questions_file,
                question_ids=args.question_id,
                categories=args.category,
                limit=args.limit,
                provider=provider,
                include_answers=args.answers,
            )
        except (EvaluationError, ProviderConfigurationError) as exc:
            print(f"Evaluation failed: {exc}")
            return 2
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
        else:
            dataset = report["dataset"]
            print("\nSTAGE 4.4 RAG EVALUATION\n")
            print(f"Mode:               {report['evaluation_mode']}")
            print(f"Questions:          {dataset['size']}")
            print(f"Verified:           {dataset['verified_ground_truth']}")
            print(f"Unverified:         {dataset['unverified_ground_truth']}")
            print(f"Failures recorded:  {report['failures']['count']}")
            print("Overall RAG accuracy is intentionally not reported.")
            destination = args.output_dir or settings.data_dir / "evaluation"
            print(f"\nOutputs:\n    {destination}")
        return 0
    if args.command == "status":
        print_report(store)
        return 0
    if args.command == "validate":
        result = audit_corpus(settings, store)
        print(f"Validation: {result['valid']} valid, {result['invalid']} invalid, {result['duplicates']} duplicate-content records")
        print_report(store)
        return 1 if result["invalid"] else 0
    if args.command == "coverage":
        report = build_coverage_report(settings, store)
        print(json.dumps(report, indent=2))
        return 0
    if args.command == "extract":
        report = run_extraction(settings, store, product_id=args.product, all_documents=args.all)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1 if report["failed_pdfs"] else 0
    if args.command == "metadata-check":
        report = build_metadata_consistency_report(settings, store)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if args.command == "clean":
        report = run_cleaning(settings, store, product_id=args.product, all_documents=args.all)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if args.command == "structure":
        report = run_structure(settings, product_id=args.product, all_documents=args.all)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 1 if report["documents_failed"] else 0
    if args.command == "chunk":
        config = ChunkConfig(
            target_tokens=args.target_tokens,
            soft_max_tokens=args.soft_max_tokens,
            hard_max_tokens=args.hard_max_tokens,
            overlap_tokens=args.overlap_tokens,
            tokenizer=args.tokenizer,
            embedding_max_tokens=resolve_embedding_max_tokens(args.embedding_max_tokens),
        )
        report = run_chunking(settings, product_id=args.product, all_documents=args.all, config=config)
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0
    if args.command == "chunk-v2":
        result = run_chunking_v2(
            settings,
            config=ChunkConfig(
                tokenizer="bge",
                embedding_max_tokens=resolve_embedding_max_tokens(args.embedding_max_tokens),
                overlap_tokens=args.overlap_tokens,
            ),
            product_id=args.product,
            all_documents=args.all,
        )
        report, comparison = result["report"], result["comparison"]
        print(f"\nSTAGE 3.1A CHUNKING\n")
        print(f"Tokenizer:          {report['tokenizer_detail']['tokenizer_class']} "
              f"({report['tokenizer_detail']['model']})")
        print(f"Model max length:   {report['tokenizer_detail']['model_max_length']} "
              f"(+{report['tokenizer_detail']['special_token_count']} special tokens)")
        print(f"Usable content:     {report['tokenizer_detail']['usable_content_token_budget']}")
        print(f"Safe limit:         {report['limits']['embedding_max_tokens']}")
        print(f"Old chunks:         {comparison['old_chunk_count']}")
        print(f"New chunks:         {comparison['new_chunk_count']}")
        print(f"Unchanged:          {comparison['unchanged_chunks']}")
        print(f"Split units:        {comparison['split_units']}")
        print(f"Over limit before:  {comparison['chunks_exceeding_safe_limit_before']}")
        print(f"Over limit after:   {comparison['chunks_exceeding_safe_limit_after']}")
        print(f"Truncated before:   {comparison['chunks_truncated_before']}")
        print(f"Truncated after:    {comparison['chunks_truncated_after']}")
        print(f"Max input tokens:   {report['max_embedding_input_tokens']}")
        print(f"Validation:         {'passed' if report['validation']['checks']['passed'] else 'FAILED'}")
        print(f"\nOutputs:\n    {settings.data_dir / 'processed' / 'chunks_v2'}")
        return 0
    if args.command == "embed":
        from .config import resolve_embedding_spec

        spec = resolve_embedding_spec(
            model=args.embedding_model,
            revision=args.embedding_revision,
            batch_size=args.batch_size,
            device=args.device,
        )
        report = run_embeddings(
            settings, product_id=args.product, all_documents=args.all, spec=spec, top_k=args.top_k,
            chunks_path=args.chunks_file, output_dir=args.output_dir,
        )
        print(f"\nSTAGE 3.1 EMBEDDING\n")
        print(f"Model:              {report['model']}")
        print(f"Dimension:          {report['embedding_dimension']}")
        print(f"Normalized:         {report['normalized']}")
        print(f"Batch size:         {report['batch_size']}")
        print(f"Input chunks:       {report['input_chunks']}")
        print(f"Successful:         {report['successful_embeddings']}")
        print(f"Failed:             {report['failed_embeddings']}")
        print(f"Generation time:    {report['timing']['embedding_generation_seconds']}s")
        print(f"Total runtime:      {report['timing']['total_runtime_seconds']}s")
        passed = report["validation"]["checks"]["passed"]
        print(f"Validation:         {'passed' if passed else 'FAILED'}")
        if not passed:
            failed = [k for k, v in report["validation"]["checks"].items() if k != "passed" and not v]
            print(f"Failed checks:      {failed}")
        for failure in report["failures"]:
            print(f"    failed {failure['chunk_ids']}: {failure['reason']} {failure['error']}")
        print(f"\nOutputs:\n    {report['outputs']['embeddings']}\n    {report['outputs']['report']}\n    {report['outputs']['semantic_test']}")
        return 0 if passed else 1
    if args.command == "retrieve-eval":
        from .retrieval_eval import evaluate

        embeddings_path = args.embeddings_file or (settings.embeddings_v2_dir / "embeddings.jsonl")
        store_dir = args.store_dir or settings.vector_store_dir
        try:
            report = evaluate(settings, store_dir=store_dir,
                              embeddings_path=embeddings_path, top_k=args.top_k)
        except Exception as exc:  # surfaced, not swallowed
            print(f"Retrieval evaluation failed: {exc}")
            return 1
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        print(f"\nSTAGE 3.2 RETRIEVAL SANITY\n")
        print(f"Index:              {report['index_size']} vectors, "
              f"{report['dimension']} dimensions, {report['similarity_metric']}")
        print(f"Queries:            {report['query_count']}  |  results: {report['result_count']}")
        print(f"Note:               no accuracy score is reported; there is no verified")
        print(f"                    ground truth for this corpus. Read the evidence below.")
        for entry in report["queries"]:
            print(f"\n  Q: {entry['query']}")
            for result in entry["results"]:
                location = result["clause"] or result["annex"] or "front matter"
                print(f"    {result['rank']}. {result['similarity_score']:.4f}  "
                      f"{result['standard']}  {location}  p{result['page']}")
                print(f"       {result['text'][:150].strip()}")
        print(f"\nReport:\n    {store_dir / 'retrieval_sanity_report.json'}")
        return 0
    if args.command == "index":
        from .retrieval import run_index
        from .vectorstore import VectorStoreError

        # Stage 3.1A output is the default: it is the only corpus whose chunks
        # all fit the model window, so it is the only one worth indexing.
        embeddings_path = args.embeddings_file or (settings.embeddings_v2_dir / "embeddings.jsonl")
        try:
            report = run_index(settings, embeddings_path=embeddings_path, store_dir=args.store_dir)
        except VectorStoreError as exc:
            print(f"Index failed: {exc}")
            return 1
        if args.json:
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        print(f"\nSTAGE 3.2 VECTOR INDEX\n")
        print(f"Backend:            {report['backend']} ({report['index_type']}, exact)")
        print(f"Model:              {report['embedding_model']}")
        print(f"Revision:           {report['model_revision']}")
        print(f"Dimension:          {report['dimension']}")
        print(f"Vectors:            {report['number_of_vectors']}")
        print(f"Documents:          {report['number_of_documents']}")
        print(f"Metric:             {report['similarity_metric']} "
              f"({report['similarity_implementation']})")
        print(f"Normalized:         {report['normalization']} "
              f"(max deviation {report['max_norm_deviation']:.2e})")
        print(f"Source SHA-256:     {report['source_embeddings_sha256'][:16]}")
        print(f"Filters available:  {', '.join(f for f, v in report['filter_capabilities'].items() if v['state'] != 'unavailable')}")
        print(f"\nIndex location:\n    {report['index_location']}")
        return 0
    if args.command == "search":
        from .retrieval import Retriever, RetrievalError
        from .config import resolve_embedding_spec
        from .embeddings import SentenceTransformerEmbedder
        from .vectorstore import StaleIndexError, VectorStoreError, load_index

        if not args.text:
            print("search needs a query, e.g. .venv/bin/python -m scraper search \"What is the definition of a control unit?\"")
            return 2
        embeddings_path = args.embeddings_file or (settings.embeddings_v2_dir / "embeddings.jsonl")
        store_dir = args.store_dir or settings.vector_store_dir
        filters = {
            field: value
            for field, value in (
                ("standard_number", args.filter_standard),
                ("document_id", args.filter_document),
                ("clause_number", args.filter_clause),
                ("annex_identifier", args.filter_annex),
                ("document_type", args.filter_type),
                ("product", args.filter_product),
            )
            if value
        }
        try:
            store = load_index(store_dir, embeddings_path)
            spec = resolve_embedding_spec(
                model=args.embedding_model, revision=args.embedding_revision,
                batch_size=args.batch_size, device=args.device,
            )
            retriever = Retriever(store, embedder=SentenceTransformerEmbedder(spec), spec=spec)
            results = retriever.search(args.text, top_k=args.top_k, filters=filters or None)
        except (StaleIndexError, VectorStoreError, RetrievalError) as exc:
            print(f"Search failed: {exc}")
            return 1
        if args.json:
            print(json.dumps([r.to_dict() for r in results], indent=2, ensure_ascii=False))
            return 0
        print(f"\nSTAGE 3.2 RETRIEVAL\n")
        print(f"Query:              {args.text}")
        print(f"Top-K:              {len(results)} (requested {args.top_k})")
        if filters:
            print(f"Filters:            {', '.join(f'{k}={v}' for k, v in filters.items())}")
        print(f"Metric:             cosine ({store.__class__.__name__}, exact)\n")
        for result in results:
            location = result.clause_number and f"Clause {result.clause_number}" or (
                f"Annex {result.annex_identifier}" if result.annex_identifier else "front matter"
            )
            if result.table_identifier:
                location += f" / Table {result.table_identifier}"
            title = f" - {result.clause_title}" if result.clause_title else ""
            print(f"{result.rank}. {result.similarity_score:.4f}  {location}{title}")
            print(f"   {'/'.join(result.standard_numbers) or 'no standard'}  "
                  f"pages {result.pages}  {result.chunk_id}")
            print(f"   {result.text[:220].strip()}{'...' if len(result.text) > 220 else ''}")
            print(f"   {result.source_url}")
        return 0
    if args.command in {"hybrid-search", "hybrid-eval"}:
        from .config import resolve_embedding_spec
        from .embeddings import SentenceTransformerEmbedder
        from .hybrid_eval import run_hybrid_evaluation
        from .hybrid_retrieval import (
            BM25Index, CrossEncoderReranker, HybridRetriever, RetrievalError,
        )
        from .retrieval import Retriever
        from .vectorstore import StaleIndexError, VectorStoreError, load_index

        if args.command == "hybrid-eval":
            try:
                result = run_hybrid_evaluation(
                    settings,
                    output_dir=args.output_dir,
                    candidate_k=args.candidate_k,
                    rerank_k=args.rerank_k,
                    final_k=args.top_k,
                    rrf_k=args.rrf_k,
                )
            except (StaleIndexError, VectorStoreError, RetrievalError) as exc:
                print(f"Hybrid evaluation failed: {exc}")
                return 1
            print(json.dumps({
                "output_dir": result["output_dir"],
                "reports": list(result["reports"]),
            }, indent=2, ensure_ascii=False))
            return 0

        if not args.text:
            print("hybrid-search needs a query")
            return 2
        embeddings_path = args.embeddings_file or (settings.embeddings_v2_dir / "embeddings.jsonl")
        store_dir = args.store_dir or settings.vector_store_dir
        filters = {
            field: value
            for field, value in (
                ("standard_number", args.filter_standard),
                ("document_id", args.filter_document),
                ("clause_number", args.filter_clause),
                ("annex_identifier", args.filter_annex),
                ("document_type", args.filter_type),
                ("product", args.filter_product),
            )
            if value
        }
        try:
            store = load_index(store_dir, embeddings_path)
            spec = resolve_embedding_spec(
                model=args.embedding_model, revision=args.embedding_revision,
                batch_size=args.batch_size, device=args.device,
            )
            semantic = Retriever(
                store, embedder=SentenceTransformerEmbedder(spec), spec=spec
            )
            chunks_path = args.chunks_file or (
                settings.processed_dir / "chunks_v2" / "chunks.jsonl"
            )
            lexical = BM25Index.from_jsonl(chunks_path, metadata_records=store.metadata)
            retriever = HybridRetriever(semantic, lexical, CrossEncoderReranker())
            results, timings = retriever.search(
                args.text,
                candidate_k=args.candidate_k,
                rerank_k=args.rerank_k,
                final_k=args.top_k,
                rrf_k=args.rrf_k,
                filters=filters or None,
            )
        except (StaleIndexError, VectorStoreError, RetrievalError) as exc:
            print(f"Hybrid search failed: {exc}")
            return 1
        payload = {"query": args.text, "timings": timings,
                   "results": [result.to_dict() for result in results]}
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0
    selected = _selected(args)
    client = PoliteClient(settings, store.log)
    if args.command == "laboratories":
        records = LIMSCollector(settings, client).collect(selected)
        collected = sum(r.get("status") == "collected" and r.get("product_id") in selected for r in records)
        print(f"Laboratory records collected for selection: {collected}")
        return 0
    stats = Counter()
    downloaded_files: list[str] = []
    if args.command in {"discover", "run"}:
        print("[DISCOVERY] Targeted official BIS discovery")
        Discoverer(settings, store, client).discover(selected, include_common=True)
    if args.command in {"download", "run", "retry-failed"}:
        print("[DOWNLOAD] Fetching HIGH and MEDIUM candidates")
        downloader = Downloader(settings, store, client)
        stats.update(downloader.download(selected, retry_failed=args.command == "retry-failed"))
        downloaded_files = downloader.downloaded_files
        print("[LIMS] Collecting scoped laboratory metadata")
        LIMSCollector(settings, client).collect(selected)
    print_report(store, stats, downloaded_files)
    return 0
