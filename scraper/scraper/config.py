from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Product:
    id: str
    label: str
    standard_numbers: tuple[str, ...]
    keywords: tuple[str, ...]
    seeds: tuple[str, ...] = ()


PRODUCTS: dict[str, Product] = {
    "mixer": Product(
        id="mixer",
        label="Mixer",
        standard_numbers=("IS 4250:2025",),
        keywords=("4250", "electric food mixer", "electric food-mixer", "liquidizer", "grinder", "centrifugal juicer"),
        seeds=(
            "https://www.bis.gov.in/is-4250-2025/?lang=en",
            "https://www.bis.gov.in/product-certification/product-specific-guidelines/?lang=en",
            "https://www.services.bis.gov.in/tmp/Circular_R7Yy_2025-04-04.pdf",
        ),
    ),
    "kettle": Product(
        id="kettle",
        label="Electric kettle",
        standard_numbers=("IS 367:1993",),
        keywords=("electric kettle", "electric kettles", "electric kettles and jugs"),
        seeds=(
            "https://www.bis.gov.in/product-certification/product-specific-guidelines/?lang=en",
            "https://www.bis.gov.in/wp-content/uploads/2022/03/Bilingual-PM-367-compressed-1.pdf",
            "https://www.bis.gov.in/wp-content/uploads/2022/04/AIF_367.pdf",
            "https://www.bis.gov.in/wp-content/uploads/2022/03/234684.pdf",
            "https://services.bis.gov.in/tmp/compendium_2025-06-10-02-16-23.pdf",
            "https://services.bis.gov.in/tmp/SR367.pdf",
            "https://services.bis.gov.in/tmp/tbl5_2024-11-12-12-30.pdf",
        ),
    ),
    "pressure_cooker": Product(
        id="pressure_cooker",
        label="Pressure cooker",
        # These versions are deliberately distinct evidence. Never collapse them.
        standard_numbers=("IS 2347:2023", "IS 2347:2017"),
        keywords=("domestic pressure cooker", "pressure cooker"),
        seeds=(
            "https://www.bis.gov.in/product-certification/product-specific-guidelines/?lang=en",
            "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/scheme-1/?lang=en",
            "https://www.bis.gov.in/wp-content/uploads/2020/01/Pressure_cooker_QCO.pdf",
            "https://www.bis.gov.in/wp-content/uploads/2023/01/Revised-PM-IS-2347.pdf",
        ),
    ),
}

COMMON_SEEDS = (
    "https://www.bis.gov.in/product-certification/product-certification-overview/?lang=en",
    "https://www.bis.gov.in/product-certification/product-certification-process/?lang=en",
    "https://www.bis.gov.in/product-certification/product-certification-faq/?lang=en",
    "https://www.bis.gov.in/product-certification/product-specific-information-2/?lang=en",
    "https://www.bis.gov.in/product-certification/products-under-compulsory-certification/scheme-i-mark-scheme/?lang=en",
    "https://www.bis.gov.in/hallmarking-overview/hallmarking-faqs/hallmarking-faq/?lang=en",
    "https://www.bis.gov.in/hallmarking-overview/consumer-protection/?lang=en",
    "https://www.bis.gov.in/consumer-overview/online-complaint-registration/?lang=en",
    "https://www.bis.gov.in/training-2/overview-of-nits/?lang=en",
    "https://www.bis.gov.in/training-2/procedure-for-applying-for-a-training-programme/?lang=en",
    "https://www.services.bis.gov.in/tmp/Revised_std_club_guidelines.pdf",
    "https://standards.bis.gov.in/",
    "https://www.bis.gov.in/homes-new/?lang=en",
)

LIMS_URLS = {
    "mixer": "https://lims.bis.gov.in/home/search_is_number/?is_number__doc_no=4250",
    "kettle": "https://lims.bis.gov.in/home/search_is_number/?is_number__doc_no=367",
    "pressure_cooker": "https://lims.bis.gov.in/home/search_is_number/?is_number__doc_no=2347",
}

APPROVED_DOMAINS = frozenset({
    "bis.gov.in", "www.bis.gov.in", "services.bis.gov.in", "www.services.bis.gov.in",
    "lims.bis.gov.in", "standards.bis.gov.in",
})


@dataclass
class Settings:
    data_dir: Path = field(default_factory=lambda: Path("data"))
    request_delay: float = 1.0
    timeout: float = 30.0
    max_retries: int = 3
    max_pages_per_domain: int = 30
    max_depth: int = 1
    max_documents_per_product: int = 25
    user_agent: str = "Specengine-BIS-Corpus-Collector/0.1 (+evidence research; respectful crawler)"
    min_pdf_size: int = 512

    @property
    def metadata_dir(self) -> Path:
        return self.data_dir / "metadata"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def chunks_dir(self) -> Path:
        return self.processed_dir / "chunks"

    @property
    def embeddings_dir(self) -> Path:
        return self.processed_dir / "embeddings"

    @property
    def embeddings_v2_dir(self) -> Path:
        return self.processed_dir / "embeddings_v2"

    @property
    def vector_store_dir(self) -> Path:
        return self.processed_dir / "vector_store"


# --------------------------------------------------------------------------
# Stage 3.1 embedding configuration
#
# The embedding model is configuration, never a hard-coded constant. Every
# selectable model is registered here with a pinned revision so a rerun on a
# different machine resolves to identical weights. The dimension is NOT stored
# here on purpose: it is read from the loaded model at runtime so a silent
# architecture change cannot corrupt the vector store.
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class EmbeddingModelSpec:
    key: str
    name: str
    provider: str
    revision: str
    normalize: bool = True
    document_prefix: str = ""
    query_prefix: str = ""
    pooling: str = "cls"
    license: str = ""
    context_window: int | None = None
    rationale: str = ""

    def with_overrides(
        self,
        name: str | None = None,
        revision: str | None = None,
        batch_size: int | None = None,
        device: str | None = None,
        normalize: bool | None = None,
    ) -> "EmbeddingSpec":
        return EmbeddingSpec(
            model=self.key,
            model_name=name or self.name,
            provider=self.provider,
            revision=revision or self.revision,
            batch_size=batch_size or DEFAULT_EMBEDDING_BATCH_SIZE,
            device=device or DEFAULT_EMBEDDING_DEVICE,
            normalize=self.normalize if normalize is None else normalize,
            document_prefix=self.document_prefix,
            query_prefix=self.query_prefix,
            pooling=self.pooling,
            license=self.license,
            context_window=self.context_window,
        )


@dataclass(frozen=True)
class EmbeddingSpec:
    """Fully resolved embedding configuration. One instance, one run."""

    model: str
    model_name: str
    provider: str
    revision: str
    batch_size: int = 32
    device: str = "cpu"
    normalize: bool = True
    document_prefix: str = ""
    query_prefix: str = ""
    pooling: str = "cls"
    license: str = ""
    context_window: int | None = None

    def validate(self) -> None:
        if self.batch_size < 1:
            raise ValueError("embedding batch size must be at least 1")
        if not self.model_name.strip():
            raise ValueError("embedding model name must not be empty")


# The registered alternatives measured against the 83-chunk Stage 2.5 corpus
# with the Stage 3.1 sanity queries (see README "Embedding model selection").
EMBEDDING_MODELS: dict[str, EmbeddingModelSpec] = {
    "bge-base-en-v1.5": EmbeddingModelSpec(
        key="bge-base-en-v1.5",
        name="BAAI/bge-base-en-v1.5",
        provider="sentence-transformers",
        revision="a5beb1e3e68b9ab74eb54cfd186867f64f240e1a",
        pooling="cls",
        license="MIT",
        context_window=512,
        rationale=(
            "Default. 109.5M parameters, 768 dimensions, CLS pooling. Strongest "
            "retrieval quality of the locally runnable candidates on the Stage 3.1 "
            "sanity queries, symmetric document/query encoding, stock BERT "
            "architecture with no trust_remote_code, MIT licence, and small enough "
            "for CPU-only deterministic reproduction."
        ),
    ),
    "bge-small-en-v1.5": EmbeddingModelSpec(
        key="bge-small-en-v1.5",
        name="BAAI/bge-small-en-v1.5",
        provider="sentence-transformers",
        revision="5c38ec7c405ec4b44b94cc5a9bb96e735b38267a",
        pooling="cls",
        license="MIT",
        context_window=512,
        rationale=(
            "3.3x smaller and ~3x faster than bge-base at 384 dimensions. Acceptable "
            "when embedding throughput matters more than ranking quality; it ranked "
            "the IS 4250 chunk above the IS 2347 chunk for the control-unit query."
        ),
    ),
    "e5-base-v2": EmbeddingModelSpec(
        key="e5-base-v2",
        name="intfloat/e5-base-v2",
        provider="sentence-transformers",
        revision="e5base-v2",
        normalize=True,
        pooling="mean",
        license="MIT",
        context_window=512,
        rationale=(
            "Comparable 768-dimension quality, but it is an asymmetric model: it "
            "requires literal 'query: ' and 'passage: ' prefixes. Stage 3.2 retrieval "
            "would silently degrade if a caller ever embeds a document without the "
            "passage prefix, so it is not the default."
        ),
    ),
    "all-MiniLM-L6-v2": EmbeddingModelSpec(
        key="all-MiniLM-L6-v2",
        name="sentence-transformers/all-MiniLM-L6-v2",
        provider="sentence-transformers",
        revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
        pooling="mean",
        license="Apache-2.0",
        context_window=256,
        rationale=(
            "Smallest and fastest option at 384 dimensions, but the weakest on this "
            "corpus: relevant-but-not-identical control-unit clauses fell from 0.71 to "
            "0.39 cosine, and its 256-token window truncates 12 of the 83 chunks."
        ),
    ),
}

DEFAULT_EMBEDDING_MODEL = "bge-base-en-v1.5"
DEFAULT_EMBEDDING_BATCH_SIZE = 32
DEFAULT_EMBEDDING_DEVICE = "cpu"
DEFAULT_SEMANTIC_TEST_TOP_K = 5

# Fixed sanity probes. They are part of the Stage 3.1 acceptance record, so the
# list is versioned rather than tuned until the results look good.
SEMANTIC_TEST_QUERIES: tuple[str, ...] = (
    "What is the definition of a control unit?",
    "What requirements apply to electric food mixers?",
    "What testing or inspection requirements are specified?",
    "What is the scope of the licence for electric kettles?",
    "What is the sampling plan for inspection of pressure cookers?",
)


def resolve_embedding_spec(
    model: str | None = None,
    revision: str | None = None,
    batch_size: int | None = None,
    device: str | None = None,
    normalize: bool | None = None,
) -> EmbeddingSpec:
    """Resolve a run configuration from explicit values, then environment."""
    key = (model or os.environ.get("EMBEDDING_MODEL") or DEFAULT_EMBEDDING_MODEL).strip()
    registered = EMBEDDING_MODELS.get(key)
    if registered is None:
        # An unregistered Hugging Face id is still allowed so the pipeline is
        # not artificially locked to the curated list, but it loses the pinned
        # revision and licence metadata.
        registered = EmbeddingModelSpec(key=key, name=key, provider="sentence-transformers", revision="")
    if revision is None:
        revision = os.environ.get("EMBEDDING_REVISION") or None
    if batch_size is None:
        raw = os.environ.get("EMBEDDING_BATCH_SIZE")
        batch_size = int(raw) if raw and raw.strip().isdigit() else None
    if device is None:
        device = os.environ.get("EMBEDDING_DEVICE") or None
    spec = registered.with_overrides(
        name=None, revision=revision, batch_size=batch_size, device=device, normalize=normalize
    )
    spec.validate()
    return spec
