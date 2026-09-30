"""Typed contracts for the deterministic Stage 4.5 entity relationship layer."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


MetadataStatus = Literal["VERIFIED", "REQUIRES_REVIEW", "UNVERIFIED"]
EntityType = Literal[
    "source_document",
    "standard",
    "structural_unit",
    "product",
    "laboratory",
    "testing_requirement",
    "service",
    "chunk",
]


class EntityProvenance(BaseModel):
    """Trace from a derived entity or relationship to an authoritative record."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    authority: str
    source_reference: str
    document_id: str | None = None
    standard_number: str | None = None
    structural_unit_id: str | None = None
    pages: list[int] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    source_url: str | None = None
    document_hash: str | None = None


class EntityRelationship(BaseModel):
    """A directional, deterministic relationship with evidence and review state."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    relationship_id: str
    source_type: EntityType
    source_id: str
    relation: str
    target_type: EntityType
    target_id: str
    metadata_status: MetadataStatus
    review_reasons: list[str] = Field(default_factory=list)
    provenance: list[EntityProvenance] = Field(min_length=1)


class SourceDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    title: str | None
    document_type: str | None
    resource_type: str | None
    organization: str | None
    standard_numbers: list[str]
    product_id: str | None
    version: str | None
    year: int | None
    source_url: str | None
    source_reference: str | None
    document_hash: str | None
    availability_status: str | None
    validation_status: str | None
    metadata_status: MetadataStatus
    conflict_types: list[str] = Field(default_factory=list)
    review_reasons: list[str] = Field(default_factory=list)


class Standard(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    standard_id: str
    standard_number: str
    title: str | None
    year: int | None
    document_ids: list[str]
    product_ids: list[str]
    structural_unit_ids: list[str]
    testing_requirement_ids: list[str]
    metadata_status: MetadataStatus


class StructuralUnit(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    structural_unit_id: str
    document_id: str
    standard_numbers: list[str]
    unit_type: Literal[
        "front_matter", "section", "clause", "subclause", "annex", "table", "references"
    ]
    identifier: str | None
    title: str | None
    pages: list[int]
    parent_id: str | None
    child_ids: list[str]
    chunk_ids: list[str]
    source_text_reference: str
    source_start_page: int | None
    source_start_line: int | None
    metadata_status: MetadataStatus
    provenance: EntityProvenance


class ProductEntity(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    product_id: str
    label: str
    standard_numbers: list[str]
    document_ids: list[str]
    metadata_status: MetadataStatus


class LaboratoryRecognition(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    recognition_id: str
    product_id: str | None
    location: str | None
    standard_number: str | None
    standard_number_raw: str | None
    recognition_status: str | None
    testing_capability: str | None
    test_method: list[dict[str, str]] | None
    clause: list[str] | None
    source_url: str | None
    retrieved_at: str | None
    availability_status: str
    metadata_status: MetadataStatus
    provenance: EntityProvenance


class Laboratory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    laboratory_id: str
    source_laboratory_id: str | None
    name: str
    product_ids: list[str]
    recognized_standard_numbers: list[str]
    recognitions: list[LaboratoryRecognition]
    metadata_status: MetadataStatus
    provenance: list[EntityProvenance]


class TestingRequirement(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    testing_requirement_id: str
    standard_number: str
    document_id: str
    structural_unit_id: str
    requirement_type: Literal["TESTING_SECTION", "TESTING_CLAUSE", "TESTING_ANNEX", "TESTING_TABLE"]
    title: str | None
    pages: list[int]
    chunk_ids: list[str]
    metadata_status: MetadataStatus
    provenance: EntityProvenance


class Service(BaseModel):
    """Contract for a future authoritative service registry; none exists today."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    service_id: str
    name: str
    product_ids: list[str]
    standard_numbers: list[str]
    document_ids: list[str]
    laboratory_ids: list[str]
    metadata_status: MetadataStatus
    provenance: list[EntityProvenance]
