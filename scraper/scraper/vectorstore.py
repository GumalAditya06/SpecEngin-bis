"""Stage 3.2: the vector index and similarity search.

This module turns ``embeddings.jsonl`` into a searchable local index. It holds
no text of its own: the index stores one float32 row per chunk plus the
``chunk_id`` that row belongs to, and the metadata store alongside it carries
the chunk record. Losing the index therefore costs a rebuild, not provenance.

Backend choice
--------------
The default backend is an exact, exhaustive NumPy scan. At this corpus size
(97 vectors of 768 dimensions) that is 5 us per query and 291 KiB resident, so
an approximate index would add build time, a dependency, and recall loss in
exchange for speed nobody can observe. Exact search is also the only option
that makes the numbers in ``retrieval_sanity_report.json`` verifiable by hand:
the score for rank 1 is a dot product a reader can recompute.

The backend sits behind :class:`VectorStore`, so FAISS, Qdrant, or pgvector can
replace it later without touching the retrieval layer. The interface is
deliberately the smallest one that still supports metadata filtering, because a
filtering backend has to be able to express the filter and the vector search in
one pass.

Similarity
----------
Vectors are compared with cosine similarity. When every row is a unit vector,
cosine equals the dot product, so the scan uses a single matmul. That
equivalence is *verified* at build time against explicit norms rather than
assumed, and the index records which branch it took.
"""

from __future__ import annotations

import hashlib
import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from .utils import atomic_json, utc_now

INDEX_FORMAT_VERSION = 1
INDEXER_VERSION = "1.0.0"

# A row counts as normalized when its L2 norm is this close to 1.0. float32
# round-off alone lands well inside 1e-6; 1e-5 leaves room for the matmul
# without ever calling an unnormalized vector normalized.
NORMALIZATION_TOLERANCE = 1e-5

INDEX_FILENAME = "index.npz"
METADATA_FILENAME = "metadata.jsonl"
CONFIG_FILENAME = "index_config.json"
REPORT_FILENAME = "index_report.json"

# Fields copied from the chunk record into the metadata store. The index itself
# carries only chunk_id, so this list is the contract for what retrieval can
# filter on and display without going back to chunks.jsonl.
METADATA_FIELDS: tuple[str, ...] = (
    "chunk_id",
    "document_id",
    "standard_numbers",
    "document_title",
    "document_type",
    "organization",
    "section_number",
    "section_title",
    "clause_number",
    "clause_title",
    "parent_clause_number",
    "annex_identifier",
    "annex_title",
    "table_identifier",
    "pages",
    "start_page",
    "end_page",
    "source_url",
    "source_file",
    "sha256",
    "context_prefix",
    "text",
    "chunk_index",
    "chunk_count",
    "structural_type",
    "structural_path",
    "product",
)

# Fields a filter may reference. Anything else is rejected loudly rather than
# silently matching nothing.
FILTERABLE_FIELDS: tuple[str, ...] = (
    "standard_number",
    "document_id",
    "clause_number",
    "annex_identifier",
    "document_type",
    "product",
)


class VectorStoreError(RuntimeError):
    """Raised when an index cannot be built, loaded, or searched safely."""


class StaleIndexError(VectorStoreError):
    """Raised when a stored index was built from different embeddings."""


# ---------------------------------------------------------------------------
# Interface
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SearchHit:
    """One ranked result. Row order is decided by the store, not the caller."""

    index: int
    score: float


class VectorStore(ABC):
    """The smallest interface that still supports filtered cosine search.

    A backend must be able to apply a boolean row mask and a similarity search
    together. Expressing it any other way (predicate callbacks, post-filtering)
    either forces a full scan on every backend or silently returns results the
    filter would have excluded.
    """

    #: Stable identifier recorded in the index config so a persisted index can
    #: be checked against the code that claims to read it.
    backend_name: str = "abstract"

    @abstractmethod
    def add(self, chunk_id: str, vector: Sequence[float]) -> None:
        """Append one vector. Order of addition defines row order."""

    @abstractmethod
    def search(
        self,
        query_vector: Sequence[float],
        top_k: int,
        filter_mask: "np.ndarray | None" = None,
    ) -> list[SearchHit]:
        """Return up to ``top_k`` hits, best first.

        ``filter_mask`` is a boolean array over rows; ``None`` means no filter.
        Ties break on ascending row index so repeated calls are identical.
        """

    @abstractmethod
    def save(self, directory: Path) -> None:
        """Persist the index and its metadata to ``directory``."""

    @abstractmethod
    def load(self, directory: Path) -> None:
        """Restore a previously saved index."""

    @property
    @abstractmethod
    def size(self) -> int:
        """Number of indexed vectors."""

    @property
    @abstractmethod
    def dimension(self) -> int:
        """Length of each indexed vector."""

    @property
    @abstractmethod
    def chunk_ids(self) -> list[str]:
        """Chunk id per row, in row order."""

    @property
    @abstractmethod
    def normalized(self) -> bool:
        """True when every row was verified to be a unit vector."""


# ---------------------------------------------------------------------------
# Exact NumPy backend
# ---------------------------------------------------------------------------


@dataclass
class NumpyExactStore(VectorStore):
    """Exhaustive cosine search over a float32 matrix.

    Deliberately not approximate. See the module docstring for why an ANN index
    would cost accuracy and dependency without buying measurable speed at this
    corpus size.
    """

    backend_name: str = "numpy_exact_flat"
    _matrix: "np.ndarray | None" = field(default=None, repr=False)
    _ids: list[str] = field(default_factory=list, repr=False)
    _metadata: list[dict] = field(default_factory=list, repr=False)
    _normalized: bool = True
    _max_norm_deviation: float = 0.0

    def __post_init__(self) -> None:
        if self._matrix is None:
            self._matrix = np.zeros((0, 0), dtype=np.float32)

    # -- construction ------------------------------------------------------

    def add(self, chunk_id: str, vector: Sequence[float]) -> None:
        row = np.asarray(vector, dtype=np.float32)
        if row.ndim != 1:
            raise VectorStoreError(f"vector for {chunk_id} is not one-dimensional")
        if self._matrix.shape[0] == 0:
            self._matrix = np.zeros((0, row.shape[0]), dtype=np.float32)
        if row.shape[0] != self._matrix.shape[1]:
            raise VectorStoreError(
                f"vector for {chunk_id} has dimension {row.shape[0]}, "
                f"index holds {self._matrix.shape[1]}"
            )
        self._matrix = np.vstack([self._matrix, row])
        self._ids.append(str(chunk_id))

    def set_metadata(self, records: Sequence[dict]) -> None:
        """Attach the metadata store, one record per row in row order."""
        if len(records) != self.size:
            raise VectorStoreError(
                f"{len(records)} metadata records for {self.size} vectors"
            )
        self._metadata = [dict(record) for record in records]

    def finalize(self) -> None:
        """Measure normalization so the search branch is chosen from evidence.

        Called after all rows are added. Stores whether cosine can be computed
        as a plain dot product, and how far the worst row was from a unit norm.
        """
        if self.size == 0:
            self._normalized = True
            self._max_norm_deviation = 0.0
            return
        norms = np.linalg.norm(self._matrix.astype(np.float64), axis=1)
        deviation = float(np.max(np.abs(norms - 1.0))) if norms.size else 0.0
        self._max_norm_deviation = deviation
        self._normalized = bool(deviation <= NORMALIZATION_TOLERANCE)

    # -- search ------------------------------------------------------------

    def search(
        self,
        query_vector: Sequence[float],
        top_k: int,
        filter_mask: "np.ndarray | None" = None,
    ) -> list[SearchHit]:
        if self.size == 0:
            return []
        if top_k < 1:
            raise ValueError("top_k must be at least 1")
        query = np.asarray(query_vector, dtype=np.float32)
        if query.shape[0] != self.dimension:
            raise VectorStoreError(
                f"query dimension {query.shape[0]} does not match index "
                f"dimension {self.dimension}"
            )
        if filter_mask is not None:
            mask = np.asarray(filter_mask, dtype=bool)
            if mask.shape[0] != self.size:
                raise VectorStoreError(
                    f"filter mask covers {mask.shape[0]} rows, index has {self.size}"
                )
            if not mask.any():
                return []
            candidates = np.flatnonzero(mask)
        else:
            candidates = None

        if self._normalized:
            # Unit vectors: cosine is the dot product. Verified in finalize().
            scores = self._matrix @ query
        else:
            # Not assumed: fall back to explicit cosine on the norms.
            norms = np.linalg.norm(self._matrix.astype(np.float64), axis=1)
            query_norm = float(np.linalg.norm(query.astype(np.float64)))
            scores = (self._matrix.astype(np.float64) @ query.astype(np.float64)) / (
                norms * query_norm
            )
            scores = scores.astype(np.float32)

        if candidates is not None:
            scores = scores[candidates]
        # Sorting by (-score, row) makes equal scores resolve to row order, so
        # two identical calls return identical rankings.
        order = sorted(range(scores.shape[0]), key=lambda i: (-float(scores[i]), i))
        return [
            SearchHit(
                index=int(candidates[i]) if candidates is not None else int(i),
                score=float(scores[i]),
            )
            for i in order[:top_k]
        ]

    # -- properties --------------------------------------------------------

    @property
    def size(self) -> int:
        return 0 if self._matrix is None else int(self._matrix.shape[0])

    @property
    def dimension(self) -> int:
        return 0 if self._matrix is None else int(self._matrix.shape[1])

    @property
    def chunk_ids(self) -> list[str]:
        return list(self._ids)

    @property
    def normalized(self) -> bool:
        return self._normalized

    @property
    def max_norm_deviation(self) -> float:
        return self._max_norm_deviation

    @property
    def metadata(self) -> list[dict]:
        return [dict(record) for record in self._metadata]

    @property
    def matrix(self) -> "np.ndarray":
        return np.zeros((0, 0), dtype=np.float32) if self._matrix is None else self._matrix

    # -- persistence -------------------------------------------------------

    def save(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        np.savez(
            directory / INDEX_FILENAME,
            vectors=self.matrix,
            chunk_ids=np.array(self._ids, dtype=object),
        )
        metadata_path = directory / METADATA_FILENAME
        with metadata_path.open("w", encoding="utf-8") as handle:
            for record in self._metadata:
                handle.write(json.dumps(record, sort_keys=True, ensure_ascii=False) + "\n")
        atomic_json(
            directory / CONFIG_FILENAME,
            {
                "format_version": INDEX_FORMAT_VERSION,
                "indexer_version": INDEXER_VERSION,
                "backend": self.backend_name,
                "search": "exact_exhaustive",
                "metric": "cosine",
                "similarity_implementation": (
                    "dot_product_on_normalized_vectors"
                    if self._normalized
                    else "explicit_cosine_division"
                ),
                "normalized": self._normalized,
                "normalization_tolerance": NORMALIZATION_TOLERANCE,
                "max_norm_deviation": self._max_norm_deviation,
                "dimension": self.dimension,
                "size": self.size,
                "created_at": utc_now(),
            },
        )

    def load(self, directory: Path) -> None:
        index_path = directory / INDEX_FILENAME
        config_path = directory / CONFIG_FILENAME
        if not index_path.exists() or not config_path.exists():
            raise VectorStoreError(
                f"no index at {directory}; run `.venv/bin/python -m scraper index` first"
            )
        config = json.loads(config_path.read_text(encoding="utf-8"))
        if config.get("backend") != self.backend_name:
            raise VectorStoreError(
                f"index was written by backend {config.get('backend')!r}, "
                f"this is {self.backend_name!r}"
            )
        if config.get("format_version") != INDEX_FORMAT_VERSION:
            raise VectorStoreError(
                f"index format {config.get('format_version')} is not "
                f"{INDEX_FORMAT_VERSION}; rebuild the index"
            )
        with np.load(index_path, allow_pickle=True) as payload:
            self._matrix = np.asarray(payload["vectors"], dtype=np.float32)
            self._ids = [str(value) for value in payload["chunk_ids"].tolist()]
        metadata_path = directory / METADATA_FILENAME
        self._metadata = (
            [json.loads(line) for line in metadata_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if metadata_path.exists()
            else []
        )
        if len(self._ids) != self.size:
            raise VectorStoreError(
                f"index holds {self.size} vectors but {len(self._ids)} chunk ids"
            )
        self._normalized = bool(config.get("normalized", True))
        self._max_norm_deviation = float(config.get("max_norm_deviation", 0.0))


# ---------------------------------------------------------------------------
# Index construction
# ---------------------------------------------------------------------------


def read_metadata_fields(record: dict) -> dict:
    """Project an embedding record down to the retrieval metadata contract.

    Every declared field is emitted on every row, with ``None`` where the
    chunk does not carry it. A fixed schema matters: with a ragged one, a
    front-matter chunk with no ``clause_number`` would be indistinguishable
    from a chunk whose clause number is explicitly unknown, and a filter would
    have to guess which it was looking at.
    """
    return {field: record.get(field) for field in METADATA_FIELDS}


def build_store(
    records: Sequence[dict],
    product_by_document: dict[str, str] | None = None,
    backend: str = "numpy_exact_flat",
) -> NumpyExactStore:
    """Build an index from embedding records.

    ``product`` is not a field of the corpus; it is joined from the manifest by
    ``document_id`` because the manifest is the authority on which product a
    document was collected for. Any document the manifest does not cover keeps
    ``product`` as ``None`` rather than being given a made-up value.
    """
    if backend != "numpy_exact_flat":
        raise VectorStoreError(
            f"unknown backend {backend!r}; available: numpy_exact_flat"
        )
    store = NumpyExactStore()
    seen: set[str] = set()
    duplicates: list[str] = []
    metadata: list[dict] = []
    for record in records:
        chunk_id = str(record.get("chunk_id"))
        if chunk_id in seen:
            duplicates.append(chunk_id)
            continue
        seen.add(chunk_id)
        vector = record.get("embedding")
        if not vector:
            raise VectorStoreError(f"{chunk_id} has no embedding")
        store.add(chunk_id, vector)
        row = read_metadata_fields(record)
        # Joined from the manifest, never guessed. A document the manifest does
        # not cover keeps product=None so a missing value stays visible.
        row["product"] = (
            product_by_document.get(str(record.get("document_id")))
            if product_by_document
            else None
        )
        metadata.append(row)
    if duplicates:
        raise VectorStoreError(
            "duplicate chunk_id in embeddings: " + ", ".join(sorted(set(duplicates)))
        )
    store.set_metadata(metadata)
    store.finalize()
    return store


def load_source_records(embeddings_path: Path) -> tuple[list[dict], str]:
    """Read embeddings.jsonl and return the records plus the file's SHA-256.

    The hash is what makes a stale index detectable later, so it is taken from
    the file's bytes rather than recomputed from the parsed content.
    """
    if not embeddings_path.exists():
        raise VectorStoreError(
            f"{embeddings_path} not found. Run `.venv/bin/python -m scraper embed` first."
        )
    raw = embeddings_path.read_bytes()
    records = [json.loads(line) for line in raw.decode("utf-8").splitlines() if line.strip()]
    return records, hashlib.sha256(raw).hexdigest()


def load_product_map(manifest_path: Path) -> dict[str, str]:
    """document_id -> product_id, read from the collection manifest."""
    if not manifest_path.exists():
        return {}
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    if isinstance(entries, dict):
        entries = entries.get("documents", [])
    return {
        str(entry.get("document_id")): str(entry.get("product_id"))
        for entry in entries
        if entry.get("document_id") and entry.get("product_id")
    }


def index_report(
    store: NumpyExactStore,
    records: Sequence[dict],
    embeddings_path: Path,
    embeddings_sha256: str,
    directory: Path,
    product_by_document: dict[str, str] | None = None,
    chunks_path: Path | None = None,
) -> dict:
    """Assemble ``index_report.json``.

    The chunks file is hashed as well as the embeddings file. The retrieval
    chain runs chunk -> vector, so a report that pinned only the vectors could
    not say which source text produced them.
    """
    document_ids = sorted({str(record.get("document_id")) for record in records})
    model = records[0].get("embedding_model") if records else None
    revision = records[0].get("embedding_model_revision") if records else None
    revisions = {record.get("embedding_model_revision") for record in records}
    models = {record.get("embedding_model") for record in records}
    dimensions = {record.get("embedding_dimension") for record in records}
    return {
        "stage": "3.2",
        "generated_at": utc_now(),
        "embedding_model": model,
        "model_revision": revision,
        "dimension": store.dimension,
        "number_of_vectors": store.size,
        "similarity_metric": "cosine",
        "similarity_implementation": (
            "dot_product_on_normalized_vectors"
            if store.normalized
            else "explicit_cosine_division"
        ),
        "normalization": store.normalized,
        "normalization_tolerance": NORMALIZATION_TOLERANCE,
        "max_norm_deviation": store.max_norm_deviation,
        "number_of_documents": len(document_ids),
        "number_of_chunks": store.size,
        "index_type": "exact_exhaustive_flat",
        "backend": store.backend_name,
        "ann_approximation": False,
        "creation_timestamp": utc_now(),
        "source_embeddings_file": str(embeddings_path),
        "source_embeddings_sha256": embeddings_sha256,
        "source_chunks_file": str(chunks_path) if chunks_path else None,
        "source_chunks_sha256": (
            hashlib.sha256(chunks_path.read_bytes()).hexdigest()
            if chunks_path is not None and chunks_path.exists()
            else None
        ),
        "single_model_revision": len(revisions) == 1,
        "single_embedding_model": len(models) == 1,
        "single_dimension": len(dimensions) == 1,
        "index_location": str(directory),
        "documents": document_ids,
        "products_indexed": sorted(
            {product_by_document[doc] for doc in document_ids if product_by_document.get(doc)}
        ) if product_by_document else [],
        "metadata_fields": list(METADATA_FIELDS),
        "filterable_fields": list(FILTERABLE_FIELDS),
    }


def check_freshness(directory: Path, embeddings_path: Path) -> dict:
    """Compare a stored index against the embeddings file it claims to index.

    Returns a structured verdict rather than raising, so a caller can decide
    whether a stale index is fatal. ``index`` and ``search`` treat it as fatal.
    """
    report_path = directory / REPORT_FILENAME
    if not report_path.exists():
        return {"stale": True, "reason": "no index_report.json", "index_exists": False}
    report = json.loads(report_path.read_text(encoding="utf-8"))
    if not embeddings_path.exists():
        return {
            "stale": True,
            "reason": f"{embeddings_path} is missing",
            "index_exists": True,
            "indexed_sha256": report.get("source_embeddings_sha256"),
        }
    current = hashlib.sha256(embeddings_path.read_bytes()).hexdigest()
    indexed = report.get("source_embeddings_sha256")
    stale = current != indexed
    return {
        "stale": stale,
        "reason": None if not stale else "embeddings file changed since the index was built",
        "index_exists": True,
        "indexed_sha256": indexed,
        "current_sha256": current,
        "indexed_model": report.get("embedding_model"),
        "indexed_revision": report.get("model_revision"),
    }


def load_index(directory: Path, embeddings_path: Path, allow_stale: bool = False) -> NumpyExactStore:
    """Load an index, refusing to serve one built from different embeddings."""
    freshness = check_freshness(directory, embeddings_path)
    if freshness["stale"] and not allow_stale:
        raise StaleIndexError(
            f"index at {directory} is stale ({freshness['reason']}). "
            f"Indexed {freshness.get('indexed_sha256', '?')[:12]}, "
            f"on disk {freshness.get('current_sha256', '?')[:12]}. "
            "Rebuild with `.venv/bin/python -m scraper index`."
        )
    store = NumpyExactStore()
    store.load(directory)
    return store
