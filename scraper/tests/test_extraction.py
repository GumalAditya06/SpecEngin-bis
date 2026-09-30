from scraper.extraction import select_first_run_records, text_statistics


def test_text_statistics_quality_rules():
    assert text_statistics("") == (0, 0, True, "EMPTY")
    assert text_statistics("short text")[-1] == "LOW"
    assert text_statistics("This is enough extracted text to contain more than eight separate words on a page.")[-1] == "GOOD"


def test_first_run_selection_prefers_current_standard_title():
    common = {"status": "downloaded", "validation_status": "valid", "document_type": "product_manual"}
    records = [
        {**common, "document_id": "old", "product_id": "mixer", "standard_numbers": ["IS 4250:2025"], "title": "IS 4250:1980", "local_path": "data/raw/old.pdf"},
        {**common, "document_id": "mixer", "product_id": "mixer", "standard_numbers": ["IS 4250:2025"], "title": "IS 4250:2025", "local_path": "data/raw/new.pdf"},
        {**common, "document_id": "kettle", "product_id": "kettle", "standard_numbers": ["IS 367:1993"], "title": "manual", "local_path": "data/raw/kettle.pdf"},
        {**common, "document_id": "cooker", "product_id": "pressure_cooker", "standard_numbers": ["IS 2347:2023"], "title": "manual", "local_path": "data/raw/cooker.pdf"},
    ]
    assert [record["document_id"] for record in select_first_run_records(records)] == ["mixer", "kettle", "cooker"]
