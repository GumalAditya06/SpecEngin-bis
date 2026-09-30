from sqlalchemy import (
    UUID,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Table,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import TSVECTOR
from sqlalchemy.orm import declarative_base, relationship
from pgvector.sqlalchemy import Vector

import uuid

Base = declarative_base()

# Dimension reserved for chunk embeddings. pgvector enforces the size on
# Postgres; the value stays NULL until an embedding model is wired up
# (LLM/RAG work is intentionally out of scope for now).
EMBEDDING_DIM = 1536

# Hard invariant from CLAUDE.md — enforced both here (DB CHECK) and in the
# ingestion pipeline (LicenceViolationError).
LICENCE_CHECK = "licence_class IN ('full_text_ok', 'metadata_only')"

# Association table: Standard ↔ Laboratory (many-to-many)
standard_laboratory = Table(
    "standard_laboratory",
    Base.metadata,
    Column("standard_id", UUID(as_uuid=True), ForeignKey("standards.id"), nullable=False),
    Column("laboratory_id", UUID(as_uuid=True), ForeignKey("laboratories.id"), nullable=False),
    UniqueConstraint("standard_id", "laboratory_id", name="uq_std_lab"),
)

# Association table: Standard ↔ Standard (many-to-many, self-referential)
standard_related = Table(
    "standard_related",
    Base.metadata,
    Column("base_standard_id", UUID(as_uuid=True), ForeignKey("standards.id"), nullable=False),
    Column("related_standard_id", UUID(as_uuid=True), ForeignKey("standards.id"), nullable=False),
    UniqueConstraint("base_standard_id", "related_standard_id", name="uq_std_related"),
)


class TimeStampedMixin:
    created_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at = Column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class Source(TimeStampedMixin, Base):
    """A fetched origin document location.

    Immutable once created (CLAUDE.md): change detection compares content
    hashes against the latest Document version instead of mutating this row.
    """

    __tablename__ = "sources"
    __table_args__ = (
        CheckConstraint(LICENCE_CHECK, name="ck_sources_licence_class"),
        Index("ix_sources_type", "source_type"),
        Index("ix_sources_licence", "licence_class"),
        # Not unique on purpose: byte-identical content mirrored at two
        # different URLs must not collide. Change detection never scans this
        # column globally — it compares per-source document hashes.
        Index("ix_sources_content_hash", "content_hash"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    url = Column(String(500), unique=True, nullable=False)
    title = Column(String(500), nullable=False)
    publisher = Column(String(200), nullable=False)
    source_type = Column(String(50), nullable=False)
    licence_class = Column(String(20), nullable=False)
    retrieved_at = Column(DateTime(timezone=True), nullable=False)
    content_hash = Column(String(64), nullable=False)
    meta = Column(JSON, nullable=False, default=dict)

    documents = relationship(
        "Document",
        back_populates="source",
        order_by="Document.version",
        lazy="raise",
    )


class Document(TimeStampedMixin, Base):
    __tablename__ = "documents"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    source_id = Column(
        UUID(as_uuid=True),
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    # Optional link to the owning Standard.
    standard_id = Column(
        UUID(as_uuid=True),
        ForeignKey("standards.id"),
        nullable=True,
        index=True,
    )
    # Monotonic per source: re-ingesting changed content creates version+1.
    version = Column(Integer, nullable=False, default=1)
    # Hash of this version's bytes — the unit change detection compares.
    content_hash = Column(String(64), nullable=True)
    is_number = Column(String(50))
    title = Column(String(500))
    raw_text = Column(Text, nullable=True)
    parsed_at = Column(DateTime(timezone=True), nullable=False)
    licence_class = Column(String(20), nullable=False)

    __table_args__ = (
        CheckConstraint(LICENCE_CHECK, name="ck_documents_licence_class"),
        Index("ix_documents_source_version", "source_id", version, unique=True),
        Index("ix_documents_is_number", "is_number"),
        Index("ix_documents_content_hash", "content_hash"),
        Index("ix_documents_standard", "standard_id"),
    )

    source = relationship("Source", back_populates="documents", lazy="raise")
    standard = relationship("Standard", back_populates="documents", lazy="raise")
    clause_nodes = relationship(
        "ClauseNode",
        back_populates="document",
        order_by="ClauseNode.order_index",
        lazy="raise",
    )
    chunks = relationship(
        "Chunk",
        back_populates="document",
        order_by="Chunk.chunk_index",
        lazy="raise",
    )


class ClauseNode(TimeStampedMixin, Base):
    __tablename__ = "clause_nodes"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    clause_path = Column(String(50), nullable=False)
    parent_id = Column(
        UUID(as_uuid=True),
        ForeignKey("clause_nodes.id", ondelete="CASCADE"),
        nullable=True,
    )
    title = Column(String(500))
    depth = Column(Integer, nullable=False)
    order_index = Column(Integer, nullable=False)

    __table_args__ = (
        Index(
            "ix_clause_nodes_doc_path",
            "document_id",
            "clause_path",
            unique=True,
        ),
        Index("ix_clause_nodes_parent", "parent_id"),
    )

    document = relationship("Document", back_populates="clause_nodes", lazy="raise")
    parent = relationship(
        "ClauseNode",
        remote_side=[id],
        back_populates="children",
        lazy="raise",
    )
    children = relationship(
        "ClauseNode",
        back_populates="parent",
        lazy="raise",
        order_by="ClauseNode.order_index",
    )


class Chunk(TimeStampedMixin, Base):
    __tablename__ = "chunks"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    document_id = Column(
        UUID(as_uuid=True),
        ForeignKey("documents.id", ondelete="CASCADE"),
        nullable=False,
    )
    clause_node_id = Column(
        UUID(as_uuid=True),
        ForeignKey("clause_nodes.id", ondelete="SET NULL"),
        nullable=True,
    )
    chunk_index = Column(Integer, nullable=False)
    text = Column(Text, nullable=False)
    token_count = Column(Integer, nullable=False)
    # Real pgvector column (the app used to declare pgvector as a dependency
    # but store this as String(4096)). NULL until embeddings are generated.
    # A similarity index belongs with the RAG milestone, not the schema.
    embedding = Column(Vector(EMBEDDING_DIM), nullable=True)
    # PostgreSQL full-text search vector / keyword vector, populated by the ingestion pipeline.
    # On PostgreSQL: used with to_tsquery/plainto_tsquery for FTS.
    # On SQLite: stores raw chunk text for keyword matching fallback.
    search_vector = Column(Text, nullable=True)
    meta = Column(JSON, nullable=False, default=dict)

    __table_args__ = (
        UniqueConstraint("document_id", "chunk_index", name="uq_chunks_doc_index"),
        Index("ix_chunks_clause", "clause_node_id"),
        Index("ix_chunks_search_vector", "search_vector", postgresql_using="gin"),
    )

    document = relationship("Document", back_populates="chunks", lazy="raise")
    clause_node = relationship("ClauseNode", lazy="raise")


class Standard(TimeStampedMixin, Base):
    """A named Indian Standard (e.g. IS 8921:2024) that groups documents."""

    __tablename__ = "standards"
    __table_args__ = (
        Index("ix_standards_is_number", "is_number"),
        Index("ix_standards_sector", "sector"),
        Index("ix_standards_status", "status"),
        Index("ix_standards_year", "year"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    is_number = Column(String(50), nullable=False)
    title = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, default="current")
    sector = Column(String(100), nullable=True)
    year = Column(Integer, nullable=True)
    description = Column(Text, nullable=True)

    documents = relationship(
        "Document",
        back_populates="standard",
        lazy="raise",
    )
    amendments = relationship(
        "Amendment",
        back_populates="standard",
        lazy="raise",
        order_by="Amendment.effective_date.desc()",
    )
    related_standards = relationship(
        "Standard",
        secondary=standard_related,
        primaryjoin="standard_related.c.base_standard_id == Standard.id",
        secondaryjoin="standard_related.c.related_standard_id == Standard.id",
        lazy="raise",
    )
    laboratories = relationship(
        "Laboratory",
        secondary=standard_laboratory,
        lazy="raise",
        viewonly=True,
    )


class Laboratory(TimeStampedMixin, Base):
    __tablename__ = "laboratories"
    __table_args__ = (
        Index("ix_laboratories_name", "name"),
        Index("ix_laboratories_city", "city"),
        Index("ix_laboratories_recognition", "recognition_status"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name = Column(String(200), nullable=False)
    city = Column(String(100), nullable=True)
    state = Column(String(50), nullable=True)
    recognition_status = Column(
        String(20), nullable=False, default="recognized"
    )
    address = Column(String(500), nullable=True)
    contact_email = Column(String(200), nullable=True)

    standards = relationship(
        "Standard",
        secondary=standard_laboratory,
        lazy="raise",
        viewonly=True,
    )
    tests = relationship(
        "Test",
        back_populates="laboratory",
        lazy="raise",
    )


class Amendment(TimeStampedMixin, Base):
    __tablename__ = "amendments"
    __table_args__ = (
        Index("ix_amendments_standard", "standard_id"),
        Index("ix_amendments_number", "standard_id", "amendment_number"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    standard_id = Column(
        UUID(as_uuid=True),
        ForeignKey("standards.id", ondelete="CASCADE"),
        nullable=False,
    )
    amendment_number = Column(String(20), nullable=False)
    title = Column(String(500))
    effective_date = Column(DateTime(timezone=True), nullable=True)

    standard = relationship("Standard", back_populates="amendments", lazy="raise")


class CertificationScheme(TimeStampedMixin, Base):
    __tablename__ = "certification_schemes"
    __table_args__ = (
        Index("ix_cert_schemes_standard", "standard_id"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title = Column(String(500), nullable=False)
    description = Column(Text, nullable=True)
    standard_id = Column(
        UUID(as_uuid=True), ForeignKey("standards.id"), nullable=True
    )

    standard = relationship("Standard", lazy="raise")


class ComplianceCase(TimeStampedMixin, Base):
    """A tracked compliance journey for one product.

    Created from the assistant's compliance route: the product, the
    standards it maps to and a snapshot of the query that produced them.
    Progress lives in ``status`` (open / in_progress / closed); the journey
    detail is rebuildable from ``standards`` plus the linked documents.
    """

    __tablename__ = "compliance_cases"
    __table_args__ = (
        Index("ix_compliance_cases_status", "status"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    product = Column(String(500), nullable=False)
    status = Column(String(20), nullable=False, default="open")
    meta = Column(JSON, nullable=False, default=dict)


class ComplianceCaseStandard(Base):
    """Association: ComplianceCase ↔ Standard (parents linked to the case)."""

    __tablename__ = "compliance_case_standards"

    case_id = Column(
        UUID(as_uuid=True),
        ForeignKey("compliance_cases.id", ondelete="CASCADE"),
        primary_key=True,
    )
    standard_id = Column(
        UUID(as_uuid=True),
        ForeignKey("standards.id", ondelete="CASCADE"),
        primary_key=True,
    )


class ProductCategory(TimeStampedMixin, Base):
    __tablename__ = "product_categories"

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name = Column(String(200), nullable=False, unique=True)
    description = Column(Text, nullable=True)


class Test(TimeStampedMixin, Base):
    __tablename__ = "tests"
    __table_args__ = (
        Index("ix_tests_laboratory", "laboratory_id"),
        Index("ix_tests_standard", "standard_id"),
    )

    id = Column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    laboratory_id = Column(
        UUID(as_uuid=True),
        ForeignKey("laboratories.id", ondelete="CASCADE"),
        nullable=False,
    )
    standard_id = Column(
        UUID(as_uuid=True),
        ForeignKey("standards.id"),
        nullable=True,
    )
    test_name = Column(String(200), nullable=False)
    description = Column(Text, nullable=True)

    laboratory = relationship("Laboratory", back_populates="tests", lazy="raise")
    standard = relationship("Standard", lazy="raise")
