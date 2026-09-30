"""Deterministic entity integration over the existing authoritative BIS data.

This is a read-only projection. It does not retrieve, rank, embed, generate,
scrape, or mutate corpus data. Every relationship is derived from an explicit
manifest, product configuration, structured node, chunk, or LIMS field.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable, Sequence

from .config import PRODUCTS, Settings
from .entity_models import (
    EntityProvenance,
    EntityRelationship,
    EntityType,
    Laboratory,
    LaboratoryRecognition,
    MetadataStatus,
    ProductEntity,
    Service,
    SourceDocument,
    Standard,
    StructuralUnit,
    TestingRequirement,
)


STANDARD_RE = re.compile(r"^IS\s*([0-9]+)\s*(?::|\()\s*([0-9]{4})\)?$", re.IGNORECASE)
TESTING_MARKER_RE = re.compile(r"\b(?:test|testing|inspection|laboratory)\b", re.IGNORECASE)
STRUCTURAL_TYPES = frozenset(
    {"front_matter", "section", "clause", "subclause", "annex", "table", "references"}
)


class KnowledgeEntityError(ValueError):
    """Authoritative inputs are missing, malformed, or internally ambiguous."""


def normalize_standard_number(value: str | None) -> str | None:
    """Normalize only explicit ``IS number:year``/``IS number (year)`` forms."""

    if value is None:
        return None
    match = STANDARD_RE.fullmatch(" ".join(str(value).split()))
    return f"IS {match.group(1)}:{match.group(2)}" if match else None


def standard_id(standard_number: str) -> str:
    normalized = normalize_standard_number(standard_number)
    if normalized is None:
        raise KnowledgeEntityError(f"unsupported standard number: {standard_number}")
    return "standard:" + normalized.casefold().replace(" ", "-").replace(":", "-")


def _stable_id(prefix: str, *parts: str) -> str:
    payload = "\x1f".join(parts).encode("utf-8")
    return f"{prefix}:{hashlib.sha256(payload).hexdigest()[:20]}"


def _load_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        if default is not None:
            return default
        raise KnowledgeEntityError(f"authoritative data not found: {path}") from None
    except json.JSONDecodeError as exc:
        raise KnowledgeEntityError(f"invalid authoritative JSON: {path}") from exc


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        raise KnowledgeEntityError(f"authoritative data not found: {path}") from None
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise KnowledgeEntityError(f"invalid JSON at {path}:{line_number}") from exc
        if not isinstance(value, dict):
            raise KnowledgeEntityError(f"expected object at {path}:{line_number}")
        rows.append(value)
    return rows


def _ordered_unique(values: Iterable[str | None]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _status_rank(value: MetadataStatus) -> int:
    return {"VERIFIED": 0, "UNVERIFIED": 1, "REQUIRES_REVIEW": 2}[value]


def _combined_status(values: Iterable[MetadataStatus]) -> MetadataStatus:
    materialized = list(values)
    return max(materialized, key=_status_rank) if materialized else "UNVERIFIED"


class KnowledgeEntityService:
    """Read-only relationship service over frozen corpus and metadata files."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self._documents: dict[str, SourceDocument] = {}
        self._standards: dict[str, Standard] = {}
        self._products: dict[str, ProductEntity] = {}
        self._units: dict[str, StructuralUnit] = {}
        self._laboratories: dict[str, Laboratory] = {}
        self._testing: dict[str, TestingRequirement] = {}
        self._services: dict[str, Service] = {}
        self._relationships: dict[str, EntityRelationship] = {}
        self._unit_text_markers: dict[str, bool] = {}
        self._build()

    @classmethod
    def from_data_dir(cls, data_dir: str | Path) -> "KnowledgeEntityService":
        return cls(Settings(data_dir=Path(data_dir)))

    @staticmethod
    def authority_map() -> dict[str, str]:
        """Document the authoritative source used for each relationship class."""

        return {
            "source_document_fields": "data/metadata/manifest.json",
            "metadata_review_state": "data/processed/metadata/metadata_consistency_report.json",
            "product_definition": "scraper.config.PRODUCTS",
            "product_to_document": "manifest.product_id",
            "product_to_standard": "scraper.config.PRODUCTS.standard_numbers",
            "standard_to_document": "manifest.standard_numbers",
            "document_to_structural_unit": "data/processed/structured/*.json structure",
            "structural_unit_to_chunk": "data/processed/chunks_v2/chunks.jsonl structural_path",
            "standard_to_laboratory": "data/metadata/laboratories.json standard_number",
            "testing_requirement": "explicit testing markers in structured units",
            "service_relationships": "unavailable: frontend workflow copy is not an authoritative registry",
        }

    def _build(self) -> None:
        manifest_path = self.settings.metadata_dir / "manifest.json"
        manifest = _load_json(manifest_path)
        if not isinstance(manifest, list):
            raise KnowledgeEntityError(f"expected a list in {manifest_path}")
        review = self._review_records()
        self._build_documents(manifest, review, manifest_path)
        chunk_rows = _load_jsonl(self.settings.processed_dir / "chunks_v2" / "chunks.jsonl")
        unit_records = self._build_structural_units(chunk_rows)
        self._build_laboratories()
        self._build_testing_requirements(unit_records)
        self._build_products_and_standards(manifest)
        self._build_relationships(manifest)

    def _review_records(self) -> dict[str, dict[str, Any]]:
        path = self.settings.processed_dir / "metadata" / "metadata_consistency_report.json"
        report = _load_json(path, {"documents": []})
        return {
            str(record["document_id"]): record
            for record in report.get("documents", [])
            if record.get("document_id")
        }

    @staticmethod
    def _review_state(record: dict[str, Any] | None) -> tuple[MetadataStatus, list[str], list[str]]:
        if record is None:
            return "UNVERIFIED", [], []
        conflicts = [
            str(item.get("conflict_type"))
            for item in record.get("conflicts", [])
            if item.get("conflict_type")
        ]
        reasons = [str(value) for value in record.get("needs_review_reasons", []) if value]
        if record.get("metadata_conflict") or record.get("verification_status") == "needs_review":
            return "REQUIRES_REVIEW", _ordered_unique(conflicts), _ordered_unique(reasons)
        if record.get("verification_status") == "verified":
            return "VERIFIED", [], []
        return "UNVERIFIED", [], reasons

    def _build_documents(
        self,
        manifest: Sequence[dict[str, Any]],
        review: dict[str, dict[str, Any]],
        manifest_path: Path,
    ) -> None:
        for row in manifest:
            document_id = str(row.get("document_id") or "").strip()
            if not document_id:
                raise KnowledgeEntityError(f"manifest record without document_id: {manifest_path}")
            if document_id in self._documents:
                raise KnowledgeEntityError(f"duplicate document_id: {document_id}")
            status, conflicts, reasons = self._review_state(review.get(document_id))
            standards = _ordered_unique(normalize_standard_number(value) for value in row.get("standard_numbers", []))
            self._documents[document_id] = SourceDocument(
                document_id=document_id,
                title=row.get("title"),
                document_type=row.get("document_type"),
                resource_type=row.get("resource_type"),
                organization=row.get("organization"),
                standard_numbers=standards,
                product_id=row.get("product_id"),
                version=row.get("version"),
                year=row.get("year"),
                source_url=row.get("source_url"),
                source_reference=row.get("local_path"),
                document_hash=row.get("sha256"),
                availability_status=row.get("status"),
                validation_status=row.get("validation_status"),
                metadata_status=status,
                conflict_types=conflicts,
                review_reasons=reasons,
            )

    @staticmethod
    def _path_signature(path: Sequence[dict[str, Any]]) -> str:
        parts = []
        for item in path:
            unit_type = str(item.get("type") or "")
            identifier = item.get("number") or item.get("identifier") or ""
            parts.append(f"{unit_type}:{identifier}")
        return "/".join(parts)

    def _build_structural_units(self, chunks: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        chunks_by_path: dict[tuple[str, str], list[str]] = defaultdict(list)
        for row in chunks:
            document_id = str(row.get("document_id") or "")
            signature = self._path_signature(row.get("structural_path") or [])
            chunk_id = str(row.get("chunk_id") or "")
            if document_id and signature and chunk_id:
                chunks_by_path[(document_id, signature)].append(chunk_id)

        unit_records: dict[str, dict[str, Any]] = {}
        structured_dir = self.settings.processed_dir / "structured"
        for path in sorted(structured_dir.glob("*.json")):
            if path.name == "structure_report.json":
                continue
            record = _load_json(path)
            document_id = str(record.get("document_id") or "")
            if not document_id or document_id not in self._documents:
                continue
            standards = _ordered_unique(
                normalize_standard_number(value) for value in record.get("standard_numbers", [])
            )
            document = self._documents[document_id]
            status: MetadataStatus = (
                "REQUIRES_REVIEW" if record.get("metadata_conflict") else document.metadata_status
            )

            def visit(
                nodes: Sequence[dict[str, Any]],
                parent_id: str | None,
                parent_path: list[dict[str, Any]],
            ) -> list[str]:
                ids: list[str] = []
                for node in nodes:
                    unit_type = str(node.get("type") or "")
                    if unit_type not in STRUCTURAL_TYPES:
                        raise KnowledgeEntityError(f"unsupported structural type {unit_type!r} in {path}")
                    element = {"type": unit_type}
                    if node.get("number") is not None:
                        element["number"] = str(node["number"])
                    if node.get("identifier") is not None:
                        element["identifier"] = str(node["identifier"])
                    structural_path = [*parent_path, element]
                    signature = self._path_signature(structural_path)
                    unit_id = _stable_id("unit", document_id, signature)
                    if unit_id in unit_records:
                        raise KnowledgeEntityError(
                            f"ambiguous structural path {signature!r} in document {document_id}"
                        )
                    children = visit(node.get("children", []), unit_id, structural_path)
                    pages = [int(value) for value in node.get("pages", [])]
                    chunk_ids = _ordered_unique(chunks_by_path.get((document_id, signature), []))
                    source_start = node.get("source_start") or {}
                    provenance = EntityProvenance(
                        authority="structured_corpus",
                        source_reference=str(path),
                        document_id=document_id,
                        standard_number=standards[0] if len(standards) == 1 else None,
                        structural_unit_id=unit_id,
                        pages=pages,
                        chunk_ids=chunk_ids,
                        source_url=document.source_url,
                        document_hash=document.document_hash,
                    )
                    unit = StructuralUnit(
                        structural_unit_id=unit_id,
                        document_id=document_id,
                        standard_numbers=standards,
                        unit_type=unit_type,  # type: ignore[arg-type]
                        identifier=(
                            str(node.get("number") or node.get("identifier"))
                            if node.get("number") is not None or node.get("identifier") is not None
                            else None
                        ),
                        title=node.get("title"),
                        pages=pages,
                        parent_id=parent_id,
                        child_ids=children,
                        chunk_ids=chunk_ids,
                        source_text_reference=f"{path}#structure:{signature}",
                        source_start_page=source_start.get("page_number"),
                        source_start_line=source_start.get("line_number"),
                        metadata_status=status,
                        provenance=provenance,
                    )
                    unit_records[unit_id] = {"entity": unit, "node": node, "path": structural_path}
                    ids.append(unit_id)
                return ids

            visit(record.get("structure", []), None, [])

        self._units = {key: value["entity"] for key, value in unit_records.items()}
        return unit_records

    def _build_laboratories(self) -> None:
        path = self.settings.metadata_dir / "laboratories.json"
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in _load_json(path, []):
            if row.get("status") != "collected" or not row.get("laboratory_name"):
                continue
            identity = (
                f"source:{row['laboratory_id']}"
                if row.get("laboratory_id") is not None
                else f"name:{' '.join(str(row['laboratory_name']).casefold().split())}"
            )
            grouped[identity].append(row)

        for identity, rows in sorted(grouped.items()):
            names = sorted({str(row["laboratory_name"]) for row in rows})
            laboratory_id = _stable_id("laboratory", identity)
            recognitions: list[LaboratoryRecognition] = []
            for row in rows:
                raw_standard = row.get("standard_number")
                normalized = normalize_standard_number(raw_standard)
                recognition_key = "|".join(
                    str(row.get(key) or "")
                    for key in ("standard_number", "source_url", "product_id", "testing_capability", "location")
                )
                recognition_id = _stable_id("recognition", laboratory_id, recognition_key)
                status: MetadataStatus = "VERIFIED" if normalized else "REQUIRES_REVIEW"
                provenance = EntityProvenance(
                    authority="bis_lims_snapshot",
                    source_reference=str(path),
                    standard_number=normalized,
                    source_url=row.get("source_url"),
                )
                recognitions.append(
                    LaboratoryRecognition(
                        recognition_id=recognition_id,
                        product_id=row.get("product_id"),
                        location=row.get("location"),
                        standard_number=normalized,
                        standard_number_raw=raw_standard,
                        recognition_status=row.get("recognition_status"),
                        testing_capability=row.get("testing_capability"),
                        test_method=row.get("test_method"),
                        clause=row.get("clause"),
                        source_url=row.get("source_url"),
                        retrieved_at=row.get("retrieved_at"),
                        availability_status=str(row.get("status")),
                        metadata_status=status,
                        provenance=provenance,
                    )
                )
            statuses = [item.metadata_status for item in recognitions]
            if len(names) != 1:
                statuses.append("REQUIRES_REVIEW")
            ordered_recognitions = sorted(recognitions, key=lambda item: item.recognition_id)
            self._laboratories[laboratory_id] = Laboratory(
                laboratory_id=laboratory_id,
                source_laboratory_id=(
                    str(rows[0]["laboratory_id"])
                    if rows[0].get("laboratory_id") is not None
                    else None
                ),
                name=names[0],
                product_ids=sorted({item.product_id for item in recognitions if item.product_id}),
                recognized_standard_numbers=sorted(
                    {item.standard_number for item in recognitions if item.standard_number}
                ),
                recognitions=ordered_recognitions,
                metadata_status=_combined_status(statuses),
                provenance=[item.provenance for item in ordered_recognitions],
            )

    def _build_testing_requirements(self, unit_records: dict[str, dict[str, Any]]) -> None:
        for unit_id, record in unit_records.items():
            unit: StructuralUnit = record["entity"]
            node = record["node"]
            marker_text = " ".join(
                value for value in (unit.title, str(node.get("text") or "")[:600]) if value
            )
            explicit_testing = bool(TESTING_MARKER_RE.search(marker_text))
            self._unit_text_markers[unit_id] = explicit_testing
            if not explicit_testing or not unit.standard_numbers:
                continue
            requirement_type = {
                "section": "TESTING_SECTION",
                "clause": "TESTING_CLAUSE",
                "subclause": "TESTING_CLAUSE",
                "annex": "TESTING_ANNEX",
                "table": "TESTING_TABLE",
            }.get(unit.unit_type)
            if requirement_type is None:
                continue
            for standard_number in unit.standard_numbers:
                requirement_id = _stable_id("testing", standard_number, unit_id)
                self._testing[requirement_id] = TestingRequirement(
                    testing_requirement_id=requirement_id,
                    standard_number=standard_number,
                    document_id=unit.document_id,
                    structural_unit_id=unit_id,
                    requirement_type=requirement_type,  # type: ignore[arg-type]
                    title=unit.title,
                    pages=unit.pages,
                    chunk_ids=unit.chunk_ids,
                    metadata_status=unit.metadata_status,
                    provenance=unit.provenance,
                )

    def _build_products_and_standards(self, manifest: Sequence[dict[str, Any]]) -> None:
        documents_by_product: dict[str, list[str]] = defaultdict(list)
        for row in manifest:
            product_id = row.get("product_id")
            document_id = row.get("document_id")
            if product_id in PRODUCTS and document_id:
                documents_by_product[str(product_id)].append(str(document_id))
        for product_id, configured in PRODUCTS.items():
            document_ids = _ordered_unique(documents_by_product.get(product_id, []))
            statuses = [self._documents[value].metadata_status for value in document_ids]
            self._products[product_id] = ProductEntity(
                product_id=product_id,
                label=configured.label,
                standard_numbers=list(configured.standard_numbers),
                document_ids=document_ids,
                metadata_status=_combined_status(statuses),
            )

        known_standards = {
            value for product in PRODUCTS.values() for value in product.standard_numbers
        }
        known_standards.update(
            value for document in self._documents.values() for value in document.standard_numbers
        )
        known_standards.update(
            number
            for laboratory in self._laboratories.values()
            for number in laboratory.recognized_standard_numbers
        )
        known_standards.update(unit_standard for unit in self._units.values() for unit_standard in unit.standard_numbers)
        for number in sorted(known_standards):
            document_ids = sorted(
                item.document_id for item in self._documents.values() if number in item.standard_numbers
            )
            product_ids = sorted(
                item.product_id for item in self._products.values() if number in item.standard_numbers
            )
            structural_ids = sorted(
                item.structural_unit_id for item in self._units.values() if number in item.standard_numbers
            )
            testing_ids = sorted(
                item.testing_requirement_id for item in self._testing.values() if item.standard_number == number
            )
            statuses: list[MetadataStatus] = [
                self._documents[value].metadata_status for value in document_ids
            ]
            statuses.extend(self._units[value].metadata_status for value in structural_ids)
            statuses.extend(
                item.metadata_status
                for item in self._laboratories.values()
                if number in item.recognized_standard_numbers
            )
            self._standards[number] = Standard(
                standard_id=standard_id(number),
                standard_number=number,
                title=None,
                year=int(number.rsplit(":", 1)[1]),
                document_ids=document_ids,
                product_ids=product_ids,
                structural_unit_ids=structural_ids,
                testing_requirement_ids=testing_ids,
                metadata_status=_combined_status(statuses),
            )

    def _add_relationship(
        self,
        source_type: EntityType,
        source_id: str,
        relation: str,
        target_type: EntityType,
        target_id: str,
        status: MetadataStatus,
        provenance: EntityProvenance,
        review_reasons: Sequence[str] = (),
    ) -> None:
        relationship_id = _stable_id(
            "relationship", source_type, source_id, relation, target_type, target_id
        )
        existing = self._relationships.get(relationship_id)
        if existing is not None:
            provenance_by_value = {
                json.dumps(item.model_dump(mode="json"), sort_keys=True): item
                for item in [*existing.provenance, provenance]
            }
            self._relationships[relationship_id] = EntityRelationship(
                relationship_id=relationship_id,
                source_type=source_type,
                source_id=source_id,
                relation=relation,
                target_type=target_type,
                target_id=target_id,
                metadata_status=_combined_status([existing.metadata_status, status]),
                review_reasons=_ordered_unique([*existing.review_reasons, *review_reasons]),
                provenance=list(provenance_by_value.values()),
            )
            return
        self._relationships[relationship_id] = EntityRelationship(
            relationship_id=relationship_id,
            source_type=source_type,
            source_id=source_id,
            relation=relation,
            target_type=target_type,
            target_id=target_id,
            metadata_status=status,
            review_reasons=list(review_reasons),
            provenance=[provenance],
        )

    def _build_relationships(self, manifest: Sequence[dict[str, Any]]) -> None:
        manifest_path = self.settings.metadata_dir / "manifest.json"
        for row in manifest:
            document_id = str(row.get("document_id") or "")
            document = self._documents[document_id]
            provenance = EntityProvenance(
                authority="manifest",
                source_reference=str(manifest_path),
                document_id=document_id,
                source_url=document.source_url,
                document_hash=document.document_hash,
            )
            product_id = row.get("product_id")
            if product_id in self._products:
                self._add_relationship(
                    "product", str(product_id), "HAS_DOCUMENT", "source_document", document_id,
                    document.metadata_status, provenance, [*document.conflict_types, *document.review_reasons],
                )
            for number in document.standard_numbers:
                self._add_relationship(
                    "standard", standard_id(number), "HAS_DOCUMENT", "source_document", document_id,
                    document.metadata_status, provenance, [*document.conflict_types, *document.review_reasons],
                )
        for product in self._products.values():
            for number in product.standard_numbers:
                provenance = EntityProvenance(
                    authority="product_configuration",
                    source_reference="scraper.config.PRODUCTS",
                    standard_number=number,
                )
                self._add_relationship(
                    "product", product.product_id, "USES_STANDARD", "standard", standard_id(number),
                    "VERIFIED", provenance,
                )
        for unit in self._units.values():
            self._add_relationship(
                "source_document", unit.document_id, "HAS_STRUCTURAL_UNIT", "structural_unit",
                unit.structural_unit_id, unit.metadata_status, unit.provenance,
            )
            for chunk_id in unit.chunk_ids:
                self._add_relationship(
                    "structural_unit", unit.structural_unit_id, "HAS_CHUNK", "chunk",
                    f"chunk:{chunk_id}", unit.metadata_status, unit.provenance,
                )
        for laboratory in self._laboratories.values():
            for recognition in laboratory.recognitions:
                if not recognition.standard_number:
                    continue
                self._add_relationship(
                    "standard", standard_id(recognition.standard_number), "HAS_LABORATORY",
                    "laboratory", laboratory.laboratory_id, recognition.metadata_status,
                    recognition.provenance,
                )
        for requirement in self._testing.values():
            self._add_relationship(
                "standard", standard_id(requirement.standard_number), "HAS_TESTING_REQUIREMENT",
                "testing_requirement", requirement.testing_requirement_id,
                requirement.metadata_status, requirement.provenance,
            )

    def get_source_document(self, document_id: str) -> SourceDocument | None:
        return self._documents.get(document_id)

    def get_standard(self, number: str) -> Standard | None:
        normalized = normalize_standard_number(number)
        return self._standards.get(normalized) if normalized else None

    def get_product(self, product_id: str) -> ProductEntity | None:
        return self._products.get(product_id)

    def get_structural_unit(self, structural_unit_id: str) -> StructuralUnit | None:
        return self._units.get(structural_unit_id)

    def get_laboratory(self, laboratory_id: str) -> Laboratory | None:
        return self._laboratories.get(laboratory_id)

    def get_service(self, service_id: str) -> Service | None:
        return self._services.get(service_id)

    def get_documents_for_standard(self, number: str) -> list[SourceDocument]:
        standard = self.get_standard(number)
        return [self._documents[value] for value in standard.document_ids] if standard else []

    def get_standards_for_product(self, product_id: str) -> list[Standard]:
        product = self._products.get(product_id)
        return [self._standards[value] for value in product.standard_numbers if value in self._standards] if product else []

    def get_structural_units(self, document_id: str) -> list[StructuralUnit]:
        return sorted(
            (item for item in self._units.values() if item.document_id == document_id),
            key=lambda item: (item.pages[0] if item.pages else 0, item.source_start_line or 0, item.structural_unit_id),
        )

    def get_related_chunks(self, structural_unit: str | StructuralUnit) -> list[str]:
        unit = structural_unit if isinstance(structural_unit, StructuralUnit) else self._units.get(structural_unit)
        return list(unit.chunk_ids) if unit else []

    def get_laboratories_for_standard(self, number: str) -> list[Laboratory]:
        normalized = normalize_standard_number(number)
        if normalized is None:
            return []
        return sorted(
            (item for item in self._laboratories.values() if normalized in item.recognized_standard_numbers),
            key=lambda item: item.laboratory_id,
        )

    def get_testing_requirements(self, number: str) -> list[TestingRequirement]:
        normalized = normalize_standard_number(number)
        if normalized is None:
            return []
        return sorted(
            (item for item in self._testing.values() if item.standard_number == normalized),
            key=lambda item: (item.pages[0] if item.pages else 0, item.testing_requirement_id),
        )

    def get_relationships(
        self,
        entity_type: EntityType,
        entity_id: str,
        relation: str | None = None,
    ) -> list[EntityRelationship]:
        return sorted(
            (
                item for item in self._relationships.values()
                if item.source_type == entity_type
                and item.source_id == entity_id
                and (relation is None or item.relation == relation)
            ),
            key=lambda item: item.relationship_id,
        )

    def get_related_sources(self, entity_type: EntityType, entity_id: str) -> list[SourceDocument]:
        document_ids: set[str] = set()
        if entity_type == "source_document" and entity_id in self._documents:
            document_ids.add(entity_id)
        elif entity_type == "standard":
            standard = next((item for item in self._standards.values() if item.standard_id == entity_id), None)
            if standard:
                document_ids.update(standard.document_ids)
        elif entity_type == "product":
            product = self._products.get(entity_id)
            if product:
                document_ids.update(product.document_ids)
        elif entity_type == "structural_unit":
            unit = self._units.get(entity_id)
            if unit:
                document_ids.add(unit.document_id)
        elif entity_type == "testing_requirement":
            requirement = self._testing.get(entity_id)
            if requirement:
                document_ids.add(requirement.document_id)
        return [self._documents[value] for value in sorted(document_ids)]
