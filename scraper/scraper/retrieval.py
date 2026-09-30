"""Stage 3.2: query embedding, metadata filtering, and ranked results.

The retrieval layer sits on top of :mod:`scraper.vectorstore` and owns the two
things an index cannot: turning a question into a vector the same way documents
were turned into vectors, and turning a vector plus a metadata store back into
BIS evidence a human can check.

Query embedding deliberately reuses :func:`scraper.embeddings.embed_queries`,
so the model, revision, normalization, batching, and ``query_prefix`` handling
are shared code rather than a second implementation that could drift.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

import numpy as np

from .config import Settings, resolve_embedding_spec
from .embeddings import SentenceTransformerEmbedder, embed_queries
from .utils import atomic_json, utc_now
from .vectorstore import (
    FILTERABLE_FIELDS,
    VectorStore,
    VectorStoreError,
    build_store,
    check_freshness,
    index_report,
    load_index,
    load_product_map,
    load_source_records,
)

DEFAULT_TOP_K = 5
MAX_TOP_K = 100
RETRIEVAL_REPORT_FILENAME = "retrieval_sanity_report.json"

# The five retrieval probes, versioned here rather than borrowed from
# SEMANTIC_TEST_QUERIES. Four are identical to the Stage 3.1 embedding probes;
# the fifth is worded as the Stage 3.2 brief specifies ("What testing
# requirements are specified?"). Stage 3.1's list is left frozen so its own
# acceptance record stays reproducible.
RETRIEVAL_SANITY_QUERIES: tuple[str, ...] = (
    "What is the definition of a control unit?",
    "What is the scope of the licence for electric kettles?",
    "What is the sampling plan for inspection of pressure cookers?",
    "What requirements apply to electric food mixers?",
    "What testing requirements are specified?",
)


class RetrievalError(RuntimeError):
    """Raised when a query cannot be served correctly."""


@dataclass(frozen=True)
class Result:
    """One ranked piece of BIS evidence.

    Every field is sourced from the chunk record or the metadata store; nothing
    here is generated, and the chunk text is the stored source text verbatim.
    """

    rank: int
    chunk_id: str
    similarity_score: float
    document_id: str | None
    standard_numbers: list[str]
    document_title: str | None
    document_type: str | None
    organization: str | None
    section_number: str | None
    section_title: str | None
    clause_number: str | None
    clause_title: str | None
    parent_clause_number: str | None
    annex_identifier: str | None
    table_identifier: str | None
    pages: list[int]
    start_page: int | None
    end_page: int | None
    source_url: str | None
    text: str
    context_prefix: str | None
    product: str | None = None

    def to_dict(self) -> dict:
        return {
            "rank": self.rank,
            "chunk_id": self.chunk_id,
            "similarity_score": round(self.similarity_score, 6),
            "document_id": self.document_id,
            "standard_numbers": self.standard_numbers,
            "document_title": self.document_title,
            "document_type": self.document_type,
            "organization": self.organization,
            "section_number": self.section_number,
            "section_title": self.section_title,
            "clause_number": self.clause_number,
            "clause_title": self.clause_title,
            "parent_clause_number": self.parent_clause_number,
            "annex_identifier": self.annex_identifier,
            "table_identifier": self.table_identifier,
            "pages": self.pages,
            "start_page": self.start_page,
            "end_page": self.end_page,
            "source_url": self.source_url,
            "product": self.product,
            "context_prefix": self.context_prefix,
            "text": self.text,
        }


# ---------------------------------------------------------------------------
# Filtering
# ---------------------------------------------------------------------------


def _matches(field: str, value, expected) -> bool:
    """Compare one metadata field against a filter value.

    ``standard_number`` is a list-valued field, so it matches by membership. The
    rest are scalar and match by equality. A filter for a field the chunk does
    not carry never matches, rather than matching an absent value.
    """
    if value is None:
        return False
    if field == "standard_number":
        values = value if isinstance(value, list) else [value]
        return any(str(item) == str(expected) for item in values)
    return str(value) == str(expected)


def build_filter_mask(
    metadata: Sequence[dict], filters: dict | None
) -> "tuple[np.ndarray | None, list[str]]":
    """Turn a filter dict into a boolean row mask.

    Returns the mask and the list of unknown field names, so the caller can
    report a filter it cannot honour instead of silently returning everything.
    """
    if not filters:
        return None, []
    unknown = [field for field in filters if field not in FILTERABLE_FIELDS]
    known = {field: value for field, value in filters.items() if field in FILTERABLE_FIELDS}
    if not known:
        return None, unknown
    mask = np.ones(len(metadata), dtype=bool)
    for field, expected in known.items():
        column = np.array(
            [_matches(field, record.get(_stored_field(field)), expected) for record in metadata],
            dtype=bool,
        )
        mask &= column
    return mask, unknown


def _stored_field(field: str) -> str:
    """Map a filter name to the metadata field it reads.

    ``standard_number`` is the singular filter name the API exposes; the corpus
    stores the list-valued ``standard_numbers``.
    """
    return "standard_numbers" if field == "standard_number" else field


def filter_capabilities(metadata: Sequence[dict]) -> dict:
    """Report which filters are answerable from the metadata that exists.

    A field is 'supported' when every indexed row has a value for it. A field
    present on only some rows is 'partial': filtering works, but rows without
    the field are excluded rather than guessed at.
    """
    total = len(metadata) or 1
    report = {}
    for field in FILTERABLE_FIELDS:
        # The filter name is singular but the stored field is the list-valued
        # `standard_numbers`; every other filter maps to a field of its name.
        column = ("standard_numbers" if field == "standard_number" else field)
        present = sum(1 for record in metadata if record.get(column) not in (None, [], ""))
        if present == total:
            state = "supported"
        elif present == 0:
            state = "unavailable"
        else:
            state = "partial"
        report[field] = {
            "state": state,
            "stored_field": column,
            "rows_with_value": present,
            "rows_total": len(metadata),
            "coverage": round(present / total, 4),
            "example_values": sorted(
                {
                    str(value)
                    for record in metadata
                    for value in record.get(column) or []
                    if value not in (None, [], "")
                }
                if field == "standard_number"
                else sorted(
                    {
                        str(record.get(column))
                        for record in metadata
                        if record.get(column) not in (None, [], "")
                    }
                )
            )[:10],
        }
    return report


# ---------------------------------------------------------------------------
# Retriever
# ---------------------------------------------------------------------------


class Retriever:
    """Query -> vector -> ranked BIS chunks.

    ``embedder`` and ``encode`` are injectable so the tests can exercise search
    ordering and filtering without loading 110M parameters.
    """

    def __init__(
        self,
        store: VectorStore,
        embedder=None,
        spec=None,
        encode: Callable[[Sequence[str]], list[list[float]]] | None = None,
    ):
        self.store = store
        self.spec = spec
        self._embedder = embedder
        if encode is not None:
            self._encode = encode
        elif embedder is not None and spec is not None:
            # The same call the embedding stage used, so query preprocessing
            # cannot diverge from document preprocessing.
            self._encode = lambda texts: embed_queries(texts, embedder, spec)
        else:
            self._encode = None
        metadata = getattr(store, "metadata", [])
        if not metadata and store.size:
            raise VectorStoreError(
                "index has no metadata store; retrieval results would lose provenance"
            )

    def embed_query(self, query: str) -> list[float]:
        if not isinstance(query, str) or not query.strip():
            raise RetrievalError("query must be a non-empty string")
        if self._encode is None:
            raise RetrievalError(
                "no query encoder configured; pass embedder+spec or encode"
            )
        vectors = self._encode([query])
        if not vectors or not vectors[0]:
            raise RetrievalError("query embedding came back empty")
        return list(vectors[0])

    def search(self, query: str, top_k: int = DEFAULT_TOP_K, filters: dict | None = None) -> list[Result]:
        """Return up to ``top_k`` results, best first."""
        if not isinstance(top_k, int) or isinstance(top_k, bool):
            raise RetrievalError("top_k must be an integer")
        if top_k < 1:
            raise RetrievalError("top_k must be at least 1")
        if top_k > MAX_TOP_K:
            raise RetrievalError(f"top_k must be at most {MAX_TOP_K}")
        vector = self.embed_query(query)
        metadata = self.store.metadata
        mask, unknown = build_filter_mask(metadata, filters)
        if unknown:
            raise RetrievalError(
                f"unsupported filter field(s): {', '.join(sorted(unknown))}. "
                f"Available: {', '.join(FILTERABLE_FIELDS)}"
            )
        if mask is not None and not mask.any():
            return []
        hits = self.store.search(vector, top_k, filter_mask=mask)
        results = []
        for rank, hit in enumerate(hits, start=1):
            record = metadata[hit.index]
            results.append(
                Result(
                    rank=rank,
                    chunk_id=str(record.get("chunk_id") or self.store.chunk_ids[hit.index]),
                    similarity_score=hit.score,
                    document_id=record.get("document_id"),
                    standard_numbers=list(record.get("standard_numbers") or []),
                    document_title=record.get("document_title"),
                    document_type=record.get("document_type"),
                    organization=record.get("organization"),
                    section_number=record.get("section_number"),
                    section_title=record.get("section_title"),
                    clause_number=record.get("clause_number"),
                    clause_title=record.get("clause_title"),
                    parent_clause_number=record.get("parent_clause_number"),
                    annex_identifier=record.get("annex_identifier"),
                    table_identifier=record.get("table_identifier"),
                    pages=list(record.get("pages") or []),
                    start_page=record.get("start_page"),
                    end_page=record.get("end_page"),
                    source_url=record.get("source_url"),
                    text=str(record.get("text") or ""),
                    context_prefix=record.get("context_prefix"),
                    product=record.get("product"),
                )
            )
        return results


# ---------------------------------------------------------------------------
# Index building
# ---------------------------------------------------------------------------


def run_index(
    settings: Settings,
    embeddings_path: Path | None = None,
    store_dir: Path | None = None,
    chunks_path: Path | None = None,
) -> dict:
    """Build and persist the local vector index."""
    embeddings_path = embeddings_path or (settings.embeddings_dir / "embeddings.jsonl")
    if store_dir is None:
        store_dir = settings.processed_dir / "vector_store"
    if chunks_path is None:
        # chunks_v2 sits beside embeddings_v2 under processed/.
        chunks_path = embeddings_path.parent.parent / "chunks_v2" / "chunks.jsonl"
    records, embeddings_sha256 = load_source_records(embeddings_path)
    if not records:
        raise VectorStoreError(f"{embeddings_path} contains no embedding records")
    product_map = load_product_map(settings.metadata_dir / "manifest.json")
    store = build_store(records, product_by_document=product_map)

    revisions = {record.get("embedding_model_revision") for record in records}
    models = {record.get("embedding_model") for record in records}
    dimensions = {record.get("embedding_dimension") for record in records}
    problems = []
    if len(revisions) > 1:
        problems.append(f"vectors from mixed model revisions: {sorted(str(r) for r in revisions)}")
    if len(models) > 1:
        problems.append(f"vectors from mixed models: {sorted(str(m) for m in models)}")
    if len(dimensions) > 1:
        problems.append(f"vectors of mixed dimensions: {sorted(str(d) for d in dimensions)}")
    if store.dimension and dimensions and store.dimension not in dimensions:
        problems.append(f"stored dimension {store.dimension} not in {sorted(str(d) for d in dimensions)}")
    if problems:
        raise VectorStoreError(
            "refusing to index vectors that do not share one provenance: "
            + "; ".join(problems)
        )

    store_dir.mkdir(parents=True, exist_ok=True)
    store.save(store_dir)
    report = index_report(
        store, records, embeddings_path, embeddings_sha256, store_dir,
        product_by_document=product_map, chunks_path=chunks_path,
    )
    report["filter_capabilities"] = filter_capabilities(store.metadata)
    atomic_json(store_dir / "index_report.json", report)
    return report


# ---------------------------------------------------------------------------
# Sanity evaluation
# ---------------------------------------------------------------------------


def run_retrieval_sanity(
    settings: Settings,
    store_dir: Path | None = None,
    embeddings_path: Path | None = None,
    top_k: int = DEFAULT_TOP_K,
    queries: Sequence[str] | None = None,
    output_path: Path | None = None,
    store: VectorStore | None = None,
) -> dict:
    """Run the fixed queries and record the evidence, without scoring it.

    There is no accuracy number here. There is no verified ground truth for
    this corpus, so any percentage would be invented. The report exists so a
    human can read the retrieved clauses and judge them.
    """
    store_dir = store_dir or (settings.processed_dir / "vector_store")
    embeddings_path = embeddings_path or (settings.embeddings_dir / "embeddings.jsonl")
    if store is None:
        store = load_index(store_dir, embeddings_path)

    spec = resolve_embedding_spec()
    embedder = SentenceTransformerEmbedder(spec)
    retriever = Retriever(store, embedder=embedder, spec=spec)

    query_list = (
        list(queries) if queries is not None else list(RETRIEVAL_SANITY_QUERIES)
    )
    entries = []
    for query in query_list:
        results = retriever.search(query, top_k=top_k)
        entries.append(
            {
                "query": query,
                "result_count": len(results),
                "results": [
                    {
                        "rank": result.rank,
                        "chunk_id": result.chunk_id,
                        "similarity_score": round(result.similarity_score, 6),
                        "standard": "/".join(result.standard_numbers) or None,
                        "standard_numbers": result.standard_numbers,
                        "clause": result.clause_number,
                        "clause_title": result.clause_title,
                        "annex": result.annex_identifier,
                        "table": result.table_identifier,
                        "page": result.start_page,
                        "pages": result.pages,
                        "source_url": result.source_url,
                        "product": result.product,
                        "text": result.text,
                    }
                    for result in results
                ],
            }
        )

    freshness = check_freshness(store_dir, embeddings_path)
    report = {
        "stage": "3.2",
        "generated_at": utc_now(),
        "note": (
            "Retrieval evidence only. No accuracy or relevance score is "
            "reported: this corpus has no manually verified ground truth, so "
            "any percentage would be fabricated. Judge these results by "
            "reading the retrieved clauses."
        ),
        "model": spec.model_name,
        "model_revision": spec.revision,
        "query_preprocessing": {
            "function": "scraper.embeddings.embed_queries",
            "shared_with_document_embedding": True,
            "query_prefix": spec.query_prefix,
            "document_prefix": spec.document_prefix,
            "symmetric_encoding": not spec.query_prefix and not spec.document_prefix,
            "normalized": spec.normalize,
        },
        "similarity_metric": "cosine",
        "similarity_implementation": (
            "dot_product_on_normalized_vectors"
            if getattr(store, "normalized", True)
            else "explicit_cosine_division"
        ),
        "index_size": store.size,
        "dimension": store.dimension,
        "top_k": top_k,
        "query_count": len(entries),
        "result_count": sum(entry["result_count"] for entry in entries),
        "index_fresh": not freshness["stale"],
        "queries": entries,
        "filter_capabilities": filter_capabilities(getattr(store, "metadata", [])),
    }
    if output_path is not None:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_json(output_path, report)
    return report
