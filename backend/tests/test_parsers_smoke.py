"""Smoke tests: every registered parser runs to completion on sample bytes.

These paths (hallmarking, lab lists, KYS) were previously unexercised and
contained runtime errors — undefined names and invalid CSS selectors — that
only surfaced when the pipeline actually dispatched to them.
"""

from app.ingestion.parsers.hallmarking_parser import HallmarkingParser
from app.ingestion.parsers.kys_parser import KysParser
from app.ingestion.parsers.lab_list_parser import LabListParser
from app.ingestion.parsers.scheme_reg_parser import SchemeRegPdfParser

from conftest import make_pdf_bytes

FAQ_HTML = (
    '<html><head><title>Hallmarking FAQs</title></head><body>'
    '<div class="accordion">'
    '<div class="accordion-item">'
    '<button class="accordion-button">What is hallmarking?</button>'
    '<div class="accordion-content">Hallmarking is quality certification of gold.</div>'
    "</div>"
    '<div class="accordion-item">'
    '<button class="accordion-button">Is it mandatory?</button>'
    '<div class="accordion-content">Mandatory hallmarking applies to jewellery.</div>'
    "</div>"
    "</div></body></html>"
).encode()

REGULATION_HTML = (
    '<html><head><title>BIS Act regulations</title></head><body>'
    "<h2>3.1 Eligibility of the applicant</h2>"
    "<p>The applicant shall fulfil the basic requirements.</p>"
    "<h2>3.2 Grant of licence</h2>"
    "<p>Licences are granted after scrutiny.</p>"
    "</body></html>"
).encode()

LAB_HTML = (
    '<html><head><title>BIS Recognised Laboratories</title></head><body>'
    "<table>"
    "<tr><th>Code</th><th>Laboratory</th></tr>"
    "<tr><td>1234567</td>"
    "<td>Central Fire Services Laboratory, Delhi, IS 2189 : 2017</td></tr>"
    "</table></body></html>"
).encode()

KYS_HTML = (
    '<html><head><title>Know Your Standard</title></head><body>'
    "<div class='standard-tile'><a href='/standards/is-8921'>IS 8921:2024 Steel wire ropes</a></div>"
    "</body></html>"
).encode()

LAB_PDF_LINES = ["1234567 Central Fire Services Laboratory Delhi IS 2189 : 2017"]


async def test_hallmarking_faq_parse():
    parser = HallmarkingParser()
    out = await parser.parse(FAQ_HTML, "https://www.bis.gov.in/hallmarking-overview/hallmarking-faqs/")
    assert out["title"]
    assert out["licence_class"] == "metadata_only"
    qa = out["metadata"]["qa_pairs"]
    assert len(qa) == 2
    assert qa[0]["question"] == "What is hallmarking?"


async def test_hallmarking_regulation_clause_extraction_runs():
    """Regression: _extract_regulation_clauses referenced an undefined `title`."""
    parser = HallmarkingParser()
    out = await parser.parse(REGULATION_HTML, "https://www.bis.gov.in/the-bureau/bis-act-rules-and-regulations/")
    assert out["licence_class"] == "metadata_only"
    clauses = out["metadata"]["clauses"]
    assert [c["path"] for c in clauses] == ["3.1", "3.2"]


async def test_lab_list_html_table_rows_are_parsed():
    """Regression: rows with <td> cells used to be skipped entirely."""
    parser = LabListParser()
    out = await parser.parse(LAB_HTML, "https://www.bis.gov.in/laboratorys/list")
    assert out["licence_class"] == "full_text_ok"
    assert out["lab_codes"] == ["1234567"]
    assert out["is_numbers"] == ["IS 2189 : 2017"]
    assert out["labs"][0]["state"] == "Delhi"


async def test_lab_list_pdf_bytes_are_parsed():
    parser = LabListParser()
    pdf = make_pdf_bytes(LAB_PDF_LINES)
    out = await parser.parse(pdf, "https://www.bis.gov.in/lab-list.pdf")
    assert out["lab_codes"] == ["1234567"]
    assert "IS 2189 : 2017" in out["is_numbers"]


async def test_kys_parse():
    parser = KysParser()
    out = await parser.parse(KYS_HTML, "https://www.bis.gov.in/know-your-standard")
    assert out["licence_class"] == "metadata_only"
    assert out["is_number"] == "IS 8921:2024"
    assert len(out["content_hash"]) == 64


async def test_scheme_reg_html_fallback():
    parser = SchemeRegPdfParser()
    out = await parser.parse(REGULATION_HTML, "https://www.bis.gov.in/certification-schemes/scheme-i")
    assert out["licence_class"] == "metadata_only"
    assert out["full_text"]
    assert "3.1" in out["clause_by_path"]


async def test_kys_search_skips_links_without_href():
    """Regression: href=None raised AttributeError on .startswith()."""
    from app.ingestion.parsers.kys_parser import parse_kys_search

    html = (
        "<div class='standard-tile'><a>no href here</a></div>"
        "<div class='standard-tile'>"
        "<a href='/standards/is-1000'>IS 1000 : 2018 Wires</a></div>"
    )
    results = await parse_kys_search(html)
    assert len(results) == 1
    assert results[0]["url"] == "https://www.bis.gov.in/standards/is-1000"
    assert results[0]["is_number"] == "IS 1000 : 2018"
