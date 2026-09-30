"""Stage 4.5 deterministic knowledge entity and relationship tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scraper.entity_models import StructuralUnit
from scraper.knowledge_entities import (
    KnowledgeEntityService,
    normalize_standard_number,
    standard_id,
)


DATA_DIR = Path("data")


@pytest.fixture(scope="module")
def entities() -> KnowledgeEntityService:
    return KnowledgeEntityService.from_data_dir(DATA_DIR)


def test_source_document_identity_comes_from_manifest(entities):
    document = entities.get_source_document("ba52fdad4caf5bc088bd")
    assert document is not None
    assert document.document_id == "ba52fdad4caf5bc088bd"
    assert document.standard_numbers == ["IS 367:1993"]
    assert document.source_url == (
        "https://www.bis.gov.in/wp-content/uploads/2022/03/"
        "Bilingual-PM-367-compressed-1.pdf"
    )
    assert document.document_hash == "0edc5ddcc7346739bccf89bc94609bb31f498b748af8bf66e7161fe5700d8979"


def test_standard_identity_is_normalized_and_deterministic(entities):
    assert normalize_standard_number("IS 367 (1993)") == "IS 367:1993"
    assert normalize_standard_number("not a standard") is None
    by_colon = entities.get_standard("IS 367:1993")
    by_parentheses = entities.get_standard("IS 367 (1993)")
    assert by_colon == by_parentheses
    assert by_colon is not None
    assert by_colon.standard_id == standard_id("IS 367:1993")
    assert by_colon.year == 1993


def test_product_to_document_relationship_uses_manifest(entities):
    product = entities.get_product("kettle")
    assert product is not None
    assert "ba52fdad4caf5bc088bd" in product.document_ids
    relationships = entities.get_relationships("product", "kettle", "HAS_DOCUMENT")
    assert any(item.target_id == "ba52fdad4caf5bc088bd" for item in relationships)
    assert all(item.provenance[0].authority == "manifest" for item in relationships)


def test_standard_to_document_relationship(entities):
    documents = entities.get_documents_for_standard("IS 2347:2023")
    assert [item.document_id for item in documents] == ["bc5e2c188eaf267d3871"]
    standard = entities.get_standard("IS 2347:2023")
    relationships = entities.get_relationships("standard", standard.standard_id, "HAS_DOCUMENT")
    assert [item.target_id for item in relationships] == ["bc5e2c188eaf267d3871"]


def test_document_to_structural_units_preserves_hierarchy(entities):
    units = entities.get_structural_units("ba52fdad4caf5bc088bd")
    assert units
    clause = next(item for item in units if item.unit_type == "clause" and item.identifier == "4")
    assert clause.parent_id is not None
    parent = entities.get_structural_unit(clause.parent_id)
    assert parent is not None and parent.unit_type == "annex" and parent.identifier == "C"
    assert clause.structural_unit_id in parent.child_ids


def test_clause_to_chunk_relationship(entities):
    clause = next(
        item
        for item in entities.get_structural_units("ba52fdad4caf5bc088bd")
        if item.unit_type == "clause" and item.identifier == "4"
    )
    assert entities.get_related_chunks(clause) == ["3fd5682dddf4ec2fd3915f31"]
    relationships = entities.get_relationships("structural_unit", clause.structural_unit_id, "HAS_CHUNK")
    assert [item.target_id for item in relationships] == ["chunk:3fd5682dddf4ec2fd3915f31"]


def test_standard_to_laboratory_relationship_preserves_lims_metadata(entities):
    laboratories = entities.get_laboratories_for_standard("IS 367:1993")
    assert laboratories
    assert all("IS 367:1993" in item.recognized_standard_numbers for item in laboratories)
    assert all(
        provenance.authority == "bis_lims_snapshot"
        for item in laboratories
        for provenance in item.provenance
    )
    assert any(
        isinstance(recognition.test_method, list)
        for item in laboratories
        for recognition in item.recognitions
    )


def test_one_laboratory_can_have_multiple_standard_recognitions(entities):
    laboratory = next(
        item for item in entities._laboratories.values() if item.source_laboratory_id == "142"
    )
    assert laboratory.name == "SHRI KRISHNA TEST HOUSE PVT LTD"
    assert laboratory.recognized_standard_numbers == [
        "IS 2347:2017",
        "IS 2347:2023",
        "IS 4250:2025",
    ]
    assert len(laboratory.recognitions) == 3


def test_standard_to_testing_requirement_is_explicit_and_traceable(entities):
    requirements = entities.get_testing_requirements("IS 4250:2025")
    assert requirements
    assert all(item.document_id == "d61562a653c02bee77be" for item in requirements)
    assert all(item.structural_unit_id for item in requirements)
    assert all(item.provenance.authority == "structured_corpus" for item in requirements)
    assert any(item.requirement_type == "TESTING_TABLE" for item in requirements)


def test_service_contract_does_not_duplicate_frontend_editorial_registry(entities):
    assert entities.get_service("testing-guidance") is None
    assert entities.get_relationships("service", "testing-guidance") == []
    assert entities.authority_map()["service_relationships"].startswith("unavailable")


def test_missing_relationships_return_empty_collections(entities):
    assert entities.get_documents_for_standard("IS 99999:2099") == []
    assert entities.get_standards_for_product("unknown") == []
    assert entities.get_structural_units("unknown") == []
    assert entities.get_related_chunks("unit:unknown") == []
    assert entities.get_laboratories_for_standard("IS 99999:2099") == []
    assert entities.get_testing_requirements("IS 99999:2099") == []


def test_metadata_conflict_is_not_hidden(entities):
    conflicted = entities.get_source_document("2e278da9542436da23e7")
    assert conflicted is not None
    assert conflicted.metadata_status == "REQUIRES_REVIEW"
    assert "standard_number_mismatch" in conflicted.conflict_types
    standard = entities.get_standard("IS 4250:2025")
    relationship = next(
        item
        for item in entities.get_relationships("standard", standard.standard_id, "HAS_DOCUMENT")
        if item.target_id == conflicted.document_id
    )
    assert relationship.metadata_status == "REQUIRES_REVIEW"
    assert "standard_number_mismatch" in relationship.review_reasons


def test_all_generated_ids_are_deterministic_and_unique(entities):
    repeated = KnowledgeEntityService.from_data_dir(DATA_DIR)
    assert set(entities._units) == set(repeated._units)
    assert set(entities._laboratories) == set(repeated._laboratories)
    assert set(entities._testing) == set(repeated._testing)
    assert set(entities._relationships) == set(repeated._relationships)
    assert len(entities._relationships) == len(set(entities._relationships))


def test_provenance_preserves_source_fields_without_copying_text(entities):
    unit = next(iter(entities.get_structural_units("bc5e2c188eaf267d3871")))
    document = entities.get_source_document(unit.document_id)
    assert unit.provenance.document_id == document.document_id
    assert unit.provenance.source_url == document.source_url
    assert unit.provenance.document_hash == document.document_hash
    assert unit.source_text_reference.startswith("data/processed/structured/")
    assert "text" not in unit.model_dump()


def test_no_missing_metadata_is_fabricated(entities):
    document = entities.get_source_document("ba52fdad4caf5bc088bd")
    standard = entities.get_standard("IS 367:1993")
    assert document.version is None
    assert document.year is None
    assert standard.title is None
    assert entities.get_service("imaginary") is None


def test_unknown_entities_return_none_or_no_sources(entities):
    assert entities.get_source_document("missing") is None
    assert entities.get_standard("unknown") is None
    assert entities.get_product("missing") is None
    assert entities.get_structural_unit("missing") is None
    assert entities.get_laboratory("missing") is None
    assert entities.get_related_sources("product", "missing") == []


def test_related_sources_use_existing_document_entities(entities):
    standard = entities.get_standard("IS 2347:2023")
    sources = entities.get_related_sources("standard", standard.standard_id)
    assert [item.document_id for item in sources] == ["bc5e2c188eaf267d3871"]
    requirement = entities.get_testing_requirements("IS 2347:2023")[0]
    sources = entities.get_related_sources("testing_requirement", requirement.testing_requirement_id)
    assert [item.document_id for item in sources] == [requirement.document_id]


def test_authority_map_names_every_relationship_source(entities):
    authority = entities.authority_map()
    assert authority["product_to_document"] == "manifest.product_id"
    assert authority["structural_unit_to_chunk"].endswith("structural_path")
    assert authority["standard_to_laboratory"].endswith("standard_number")


def test_models_reject_mutation(entities):
    unit: StructuralUnit = next(iter(entities.get_structural_units("d61562a653c02bee77be")))
    with pytest.raises(Exception):
        unit.document_id = "changed"


def test_frozen_corpus_files_are_only_read(tmp_path):
    # The service accepts copied authoritative inputs and produces no output files.
    data = tmp_path / "data"
    (data / "metadata").mkdir(parents=True)
    (data / "processed/metadata").mkdir(parents=True)
    (data / "processed/chunks_v2").mkdir(parents=True)
    (data / "processed/structured").mkdir(parents=True)
    (data / "metadata/manifest.json").write_text("[]")
    (data / "metadata/laboratories.json").write_text("[]")
    (data / "processed/metadata/metadata_consistency_report.json").write_text(
        json.dumps({"documents": []})
    )
    (data / "processed/chunks_v2/chunks.jsonl").write_text("")
    before = sorted(path.relative_to(data) for path in data.rglob("*") if path.is_file())
    service = KnowledgeEntityService.from_data_dir(data)
    after = sorted(path.relative_to(data) for path in data.rglob("*") if path.is_file())
    assert before == after
    assert service.get_standard("IS 367:1993") is not None  # configured product authority
