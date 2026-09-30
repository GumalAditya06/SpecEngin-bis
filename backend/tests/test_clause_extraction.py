"""Clause extraction: detector, tree building, and the extractor stage."""

from app.ingestion.parsers.pdf_parser import (
    build_clause_tree_from_lines,
    detect_clause_path,
    extract_text_with_clauses_from_bytes,
)
from app.ingestion.parsers.scheme_reg_parser import (
    detect_clause_path as scheme_detect,
)
from app.ingestion.pipeline import ClauseExtractor

from conftest import SAMPLE_PDF_LINES, make_pdf_bytes


def test_detect_clause_path_matches_dotted_path():
    path, title = detect_clause_path("5.2.1 Submarine cables")
    assert path == "5.2.1"
    assert title == "Submarine cables"


def test_detect_requires_at_least_one_sub_level():
    # Top-level numbers like "1 Scope" are headings, not dotted clauses.
    assert detect_clause_path("1 Scope") is None
    assert detect_clause_path("plain prose line") is None
    assert detect_clause_path("") is None


def test_detect_is_whitespace_tolerant():
    path, title = detect_clause_path("   3.4   Marking requirements   ")
    assert path == "3.4"
    assert title == "Marking requirements"


def test_scheme_reg_detects_alphabetic_subclause():
    path, title = scheme_detect("5.1(a) Application")
    assert path == "5.1(a)"
    assert title == "Application"


def test_scheme_reg_detects_three_level_path():
    """Regression: the dotted group wasn't quantified, so 5.2.1 never matched."""
    path, title = scheme_detect("5.2.1 Grade A")
    assert path == "5.2.1"
    assert title == "Grade A"


def test_indigenous_pdf_detect_matches_its_docstring_example():
    from app.ingestion.parsers.indigenous_pdf_parser import (
        detect_clause_path as indigenous_detect,
    )

    path, title = indigenous_detect("5.2.1 Title")
    assert path == "5.2.1"
    assert title == "Title"


def test_build_tree_assigns_depth_and_parent_path():
    clauses = build_clause_tree_from_lines(
        ["5.1 Scope", "5.2 Material", "5.2.1 Grade A", "noise line"]
    )
    by_path = {c["path"]: c for c in clauses}
    assert set(by_path) == {"5.1", "5.2", "5.2.1"}
    assert by_path["5.1"]["parent_path"] is None
    assert by_path["5.2.1"]["parent_path"] == "5.2"
    assert by_path["5.2.1"]["depth"] == by_path["5.2"]["depth"] + 1


def test_build_tree_keeps_parents_before_children():
    clauses = build_clause_tree_from_lines(
        ["5.2.1 Grade A", "5.1 Scope"]  # child text appears first
    )
    depths = [c["depth"] for c in clauses]
    assert depths == sorted(depths), "parents must be persistable before children"


def test_extractor_prefers_parser_supplied_clauses():
    extractor = ClauseExtractor()
    supplied = [{"path": "7.3", "title": "X", "depth": 2}]
    out = extractor.extract(
        b"", "scheme_reg", {"clauses": supplied, "full_text": "ignored"}
    )
    assert out == supplied


def test_extractor_falls_back_to_line_scan_and_strips_html():
    extractor = ClauseExtractor()
    html = b"<p>3.1 Restriction on manufacture</p><p>details</p>"
    out = extractor.extract(html, "qco", {"title": "QCO"})
    assert [c["path"] for c in out] == ["3.1"]
    assert out[0]["title"] == "Restriction on manufacture"


def test_extractor_returns_empty_when_no_text():
    extractor = ClauseExtractor()
    assert extractor.extract(b"", "faq_pages", {"title": "FAQ"}) == []


def test_pdf_extraction_with_clauses():
    result = extract_text_with_clauses_from_bytes(make_pdf_bytes(SAMPLE_PDF_LINES))
    paths = [c["path"] for c in result["clauses"]]
    assert "5.1" in paths and "5.2.1" in paths
    assert "5.1" in result["clause_by_path"]
    assert "steel wire" in result["full_text"].lower()
