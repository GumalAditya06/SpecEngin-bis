"""Parser dispatch: registry mapping + Pipeline._parse routing."""

import pytest

from app.ingestion.parsers import get_parser, registry
from app.ingestion.parsers.hallmarking_parser import HallmarkingParser
from app.ingestion.parsers.indigenous_pdf_parser import (
    extract_indigenous_standard,
)
from app.ingestion.parsers.pdf_parser import IndigenousPdfParser
from app.ingestion.parsers.kys_parser import KysParser
from app.ingestion.parsers.lab_list_parser import LabListParser
from app.ingestion.parsers.qco_parser import QcoParser
from app.ingestion.parsers.scheme_reg_parser import SchemeRegPdfParser
from app.ingestion.pipeline import Pipeline

from conftest import SAMPLE_PDF_LINES, SAMPLE_QCO_HTML, make_pdf_bytes

CANONICAL_TYPES = {
    "indigenous_pdf": IndigenousPdfParser,
    "qco": QcoParser,
    "scheme_reg": SchemeRegPdfParser,
    "hallmarking_docs": HallmarkingParser,
    "lab_lists": LabListParser,
    "kys_catalogue": KysParser,
}

# CLAUDE.md source types that intentionally have no dedicated parser yet —
# the pipeline falls back to a generic record instead of crashing.
UNPARSED_TYPES = ("faq_pages", "ISO_adopted")


@pytest.mark.parametrize("source_type,parser_cls", sorted(CANONICAL_TYPES.items()))
def test_registry_maps_canonical_source_types(source_type, parser_cls):
    assert get_parser(source_type) is parser_cls


@pytest.mark.parametrize(
    "alias,parser_cls",
    [
        ("hallmarking", HallmarkingParser),  # pre-CLAUDE.md name
        ("lab_list", LabListParser),  # pre-CLAUDE.md name
    ],
)
def test_registry_keeps_legacy_aliases(alias, parser_cls):
    assert get_parser(alias) is parser_cls


@pytest.mark.parametrize("source_type", UNPARSED_TYPES)
def test_unhandled_source_types_return_none(source_type):
    assert get_parser(source_type) is None
    assert source_type not in registry.all()


def test_unknown_source_type_returns_none():
    assert get_parser("definitely_not_a_type") is None


async def test_dispatch_qco_html():
    pipe = Pipeline(db_session=None)
    parsed = await pipe._parse(
        "qco", SAMPLE_QCO_HTML, "https://www.bis.gov.in/qco/steel-2025"
    )
    assert parsed["title"]
    assert parsed["licence_class"] == "full_text_ok"
    assert "is_numbers" in parsed["metadata"]


async def test_dispatch_indigenous_pdf_extracts_clauses():
    pipe = Pipeline(db_session=None)
    pdf = make_pdf_bytes(SAMPLE_PDF_LINES)
    parsed = await pipe._parse(
        "indigenous_pdf", pdf, "https://standards.bis.gov.in/IS_12345_2024.pdf"
    )
    assert parsed["licence_class"] == "full_text_ok"
    assert "5.1 Scope" in parsed["full_text"]
    assert parsed["is_number"] == "IS 12345 : 2024"
    paths = [c["path"] for c in parsed["clauses"]]
    assert "5.1" in paths
    assert "5.2.1" in paths


async def test_dispatch_indigenous_pdf_rejects_non_pdf_bytes():
    pipe = Pipeline(db_session=None)
    with pytest.raises(ValueError, match="expected PDF bytes"):
        await pipe._parse(
            "indigenous_pdf", b"<html>not a pdf</html>", "https://x/y.pdf"
        )


async def test_dispatch_unhandled_type_falls_back_to_generic_record():
    pipe = Pipeline(db_session=None)
    parsed = await pipe._parse("faq_pages", b"", "https://bis.gov.in/faq/guide")
    assert parsed == {"title": "guide", "is_number": None}
