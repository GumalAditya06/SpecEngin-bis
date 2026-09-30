import hashlib
import json

from scraper.structure import (
    build_hierarchy,
    parse_numbered_heading,
    structure_document,
)


def page(number, text):
    return {"page_number": number, "raw_text": text, "cleaned_text": text}


def walk(nodes):
    for node in nodes:
        yield node
        yield from walk(node.get("children", []))


def test_detects_section_clause_and_subclause_hierarchy():
    structure, ambiguous, _ = build_hierarchy([
        page(1, "Foreword\n\n4 REQUIREMENTS\nIntro\n4.1 General\nBody\n4.1.1 Safety\nRequirement")
    ])
    section = next(node for node in structure if node.get("number") == "4")
    clause = next(node for node in section["children"] if node.get("number") == "4.1")
    subclause = next(node for node in clause["children"] if node.get("number") == "4.1.1")
    assert section["type"] == "section"
    assert clause["type"] == "clause"
    assert subclause["type"] == "subclause"
    assert not ambiguous


def test_detects_annex_a_and_annex_clause_a_1():
    structure, _, _ = build_hierarchy([
        page(1, "Front matter\nANNEX A\nRequirements\nA.1 General\nText\nA.1.1 Detail\nMore")
    ])
    annex = next(node for node in structure if node["type"] == "annex")
    clause = next(node for node in annex["children"] if node.get("number") == "A.1")
    subclause = next(node for node in clause["children"] if node.get("number") == "A.1.1")
    assert annex["identifier"] == "A"
    assert clause["type"] == "clause"
    assert subclause["type"] == "subclause"


def test_cross_page_clause_is_one_node_with_both_pages():
    structure, _, _ = build_hierarchy([
        page(1, "4 REQUIREMENTS\n4.1 General\nFirst part"),
        page(2, "continued text\n4.2 Construction\nNext clause"),
    ])
    clause = next(node for node in walk(structure) if node.get("number") == "4.1")
    assert clause["start_page"] == 1
    assert clause["end_page"] == 2
    assert clause["pages"] == [1, 2]
    assert "continued text" in clause["text"]


def test_numbered_table_row_is_not_a_clause():
    assert parse_numbered_heading("1 | 230 V | 50 Hz") is None
    structure, _, _ = build_hierarchy([page(1, "TABLE 1\n1 Capacity\n2 Voltage")])
    assert not any(node["type"] in {"section", "clause", "subclause"} for node in walk(structure))


def test_page_number_date_and_standard_reference_are_not_clauses():
    assert parse_numbered_heading("Page 4") is None
    assert parse_numbered_heading("04-06-2018") is None
    assert parse_numbered_heading("IS 302 (Part 1)") is None


def test_structure_output_preserves_pages_metadata_and_input(tmp_path):
    source_path = tmp_path / "cleaned.json"
    output_path = tmp_path / "structured.json"
    source = {
        "document_id": "doc",
        "source_url": "https://example.test/doc.pdf",
        "sha256": "pdf-hash",
        "metadata_conflict": True,
        "verification_status": "needs_review",
        "pages": [page(1, "4 REQUIREMENTS\n4.1 General\nText")],
    }
    source_path.write_text(json.dumps(source), encoding="utf-8")
    before = hashlib.sha256(source_path.read_bytes()).hexdigest()
    result, stats = structure_document(source_path, output_path)
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == before
    assert result["pages"] == source["pages"]
    assert result["metadata_conflict"] is True
    assert result["source_url"] == source["source_url"]
    assert result["structure_quality_checks"]["passed"]
    assert stats["pages_processed"] == 1


def test_references_heading_is_not_a_clause():
    structure, _, _ = build_hierarchy([page(1, "REFERENCES\nIS 302 (Part 1)\nIS 4250:2025")])
    nodes = list(walk(structure))
    assert any(node["type"] == "references" for node in nodes)
    assert not any(node["type"] in {"clause", "subclause"} for node in nodes)


def test_uppercase_annex_ends_a_multipage_table_context():
    structure, ambiguous, _ = build_hierarchy([
        page(1, "ANNEX C\nScheme\nTABLE 1\n1 Capacity"),
        page(2, "2 Voltage\nANNEX D\nPossible Tests in a day\n1. Capacity"),
    ])
    annexes = [node for node in structure if node["type"] == "annex"]
    assert [node["identifier"] for node in annexes] == ["C", "D"]
    assert not any(node.get("number") == "1" for node in walk(annexes[-1]["children"]))
    assert not ambiguous


def test_obvious_quantity_and_range_are_not_ambiguous_clauses():
    _, ambiguous, _ = build_hierarchy([
        page(1, "ANNEX A\nGrouping Guidelines\n2 numbers\n1 to 6.5\n6528 for Stainless Steel")
    ])
    assert not ambiguous
