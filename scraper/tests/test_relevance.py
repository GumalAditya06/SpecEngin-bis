from scraper.config import PRODUCTS
from scraper.relevance import classify


def test_pressure_versions_are_not_merged():
    old = classify("https://bis.gov.in/a.pdf", "Pressure Cooker QCO", "IS 2347:2017", PRODUCTS, "pressure_cooker")
    new = classify("https://bis.gov.in/b.pdf", "Product Manual", "IS 2347:2023 Domestic Pressure Cooker", PRODUCTS, "pressure_cooker")
    assert old.standard_numbers == ("IS 2347:2017",)
    assert new.standard_numbers == ("IS 2347:2023",)


def test_unrelated_document_is_rejected():
    result = classify("https://bis.gov.in/cement.pdf", "Cement brochure", "Portland cement", PRODUCTS)
    assert result.relevance == "REJECTED"


def test_standard_and_manual_is_high():
    result = classify("https://bis.gov.in/manual.pdf", "Product Manual IS 367:1993", "Electric kettles and jugs", PRODUCTS, "kettle")
    assert result.relevance == "HIGH"
    assert result.document_type == "product_manual"


def test_product_hint_does_not_make_unrelated_link_low():
    result = classify("https://bis.gov.in/about/", "Contact us", "generic footer", PRODUCTS, "mixer")
    assert result.relevance == "REJECTED"


def test_bare_standard_number_only_counts_in_url_or_title():
    result = classify("https://bis.gov.in/unrelated.pdf", "IS 14246:2024", "table row 367", PRODUCTS, "kettle")
    assert result.relevance == "REJECTED"


def test_table_row_number_in_title_is_not_a_standard_signal():
    result = classify("https://bis.gov.in/pm-is-14246.pdf", "View 367 IS 14246:2024", "steel coils", PRODUCTS, "kettle")
    assert result.relevance == "REJECTED"


def test_site_footer_does_not_make_common_page_relevant():
    result = classify("https://bis.gov.in/internship/", "Internship Scheme", "footer: product certification", PRODUCTS, common=True)
    assert result.relevance == "LOW"


def test_internship_scheme_does_not_match_scheme_i():
    result = classify("https://bis.gov.in/internship.pdf", "Internship Scheme Internship Scheme", "", PRODUCTS, common=True)
    assert result.relevance == "LOW"


def test_hallmarking_and_training_are_common_service_evidence():
    hallmark = classify("https://bis.gov.in/hallmarking-overview/", "Hallmarking FAQ", "", PRODUCTS, common=True)
    training = classify("https://bis.gov.in/training-2/overview-of-nits/", "Overview of NITS", "", PRODUCTS, common=True)
    assert hallmark.relevance == "MEDIUM"
    assert hallmark.document_type == "consumer_information"
    assert training.relevance == "MEDIUM"
    assert training.document_type == "technical_document"
