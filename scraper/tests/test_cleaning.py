import hashlib
import json

from scraper.cleaning import (
    _detect_type,
    _validate_cleaned_document,
    clean_extracted_document,
    clean_page_text,
    detect_repeated_marginal_lines,
    standards_in,
)


def test_standard_detection_normalizes_colon_and_parenthesis_forms():
    assert standards_in("IS 4250 : 2025 and IS 367 (1993)") == ["IS 4250:2025", "IS 367:1993"]


def test_document_identity_wins_over_embedded_product_manual_reference():
    assert _detect_type("The Product Manual shall also apply.", "Implementation Circular circular.pdf") == "implementation_circular"
    assert _detect_type("The Product Manual is referenced here.", "Additional Guidelines for Scheme-I") == "scheme_information"


def test_repeated_marginal_lines_require_document_level_repetition():
    pages = [
        {"text": f"BUREAU OF INDIAN STANDARDS\nClause content {number}\nFooter text"}
        for number in range(5)
    ]
    headers, footers = detect_repeated_marginal_lines(pages, edge_lines=1)
    assert "bureau of indian standards" in headers
    assert "footer text" in footers
    assert not any("clause content" in line for line in headers)


def test_repeated_version_header_is_retained_as_evidence():
    pages = [
        {"text": "PM/IS 4250/4/April 2025\nRequirement text\nBUREAU OF INDIAN STANDARDS"}
        for _ in range(5)
    ]
    headers, footers = detect_repeated_marginal_lines(pages, edge_lines=1)
    assert "pm/is 4250/4/april 2025" not in headers
    assert "bureau of indian standards" in footers


def test_cleaning_preserves_critical_values_and_clause_boundaries():
    raw = "  5.2.1 Requirement  \nThe appliance shall operate at 230 V and 50 Hz.\nNOTE — Keep 25 %.\nPage 2 of 4\n"
    cleaned, stats, semantic = clean_page_text(raw, set(), set())
    assert "5.2.1" in cleaned
    assert "230 V" in cleaned and "50 Hz" in cleaned and "25%" in cleaned
    assert "NOTE —" in cleaned
    assert "Page 2 of 4" not in cleaned
    assert stats["page_numbers_removed"] == 1
    assert "Page 2 of 4" not in semantic


def test_conservative_dehyphenation_and_line_joining():
    cleaned, stats, _ = clean_page_text(
        "The product shall be suit-\nable for domestic use and repeated\noperation.", set(), set()
    )
    assert "suitable" in cleaned
    assert stats["dehyphenations"] == 1


def test_standalone_table_numbers_inside_page_are_preserved():
    raw = "Document heading\nTable 1\n25\n50\n75\nRequirement text\nPage 4 of 8"
    cleaned, stats, _ = clean_page_text(raw, set(), set())
    assert "\n25\n50\n75\n" in cleaned
    assert "Page 4 of 8" not in cleaned
    assert stats["page_numbers_removed"] == 1


def test_bare_table_number_at_page_edge_is_not_treated_as_page_number():
    cleaned, stats, _ = clean_page_text("REPEATED HEADER\n27\nTest equipment row", set(), set())
    assert "\n27\n" in cleaned
    assert stats.get("page_numbers_removed", 0) == 0


def test_standalone_dash_is_not_joined_to_next_table_label():
    cleaned, stats, _ = clean_page_text("Raw material\n-\nb) Grouping Guidelines", set(), set())
    assert "\n-\nb) Grouping Guidelines" in cleaned
    assert stats.get("dehyphenations", 0) == 0


def test_explicit_page_label_is_removed_outside_margin_window():
    raw = "Address line 1\nAddress line 2\nAddress line 3\nAddress line 4\nPage 1\nRequirement"
    cleaned, stats, _ = clean_page_text(raw, set(), set())
    assert "Page 1" not in cleaned
    assert stats["page_numbers_removed"] == 1


def test_quality_checks_reject_lost_standard_or_unit():
    source = {"pages": [{"page_number": 1, "text": "5.2.1 Use IS 4250:2025 at 230 V."}]}
    cleaned = [{"page_number": 1, "cleaned_text": "5.2.1 Use the appliance."}]
    checks = _validate_cleaned_document(source, cleaned, [source["pages"][0]["text"]])
    assert not checks["standard_numbers_preserved"]
    assert not checks["units_preserved"]
    assert not checks["numerical_values_preserved"]
    assert not checks["passed"]


def test_cleaned_output_embeds_raw_text_without_modifying_extraction(tmp_path):
    extracted = tmp_path / "source.json"
    output = tmp_path / "cleaned.json"
    pages = []
    for number in range(1, 4):
        pages.append({
            "page_number": number,
            "text": f"REPEATED HEADER\n5.2.{number} The appliance shall operate at 230 V.\nNOTE — Requirement remains.\nPage {number} of 3",
        })
    extracted.write_text(json.dumps({"document_id": "doc", "product_id": "mixer", "standard_numbers": ["IS 4250:2025"], "pages": pages}), encoding="utf-8")
    before = hashlib.sha256(extracted.read_bytes()).hexdigest()
    result, stats = clean_extracted_document(
        extracted, output,
        {"manifest_standard": ["IS 4250:2025"], "document_detected_standard": ["IS 4250:2025"],
         "metadata_conflict": False, "verification_status": "verified", "conflict_type": None},
    )
    assert hashlib.sha256(extracted.read_bytes()).hexdigest() == before
    assert len(result["pages"]) == 3
    assert result["pages"][0]["raw_text"] == pages[0]["text"]
    assert result["pages"][0]["page_number"] == 1
    assert stats["headers_removed"] == 3
    assert stats["page_numbers_removed"] == 3
    assert result["quality_checks"]["passed"]
