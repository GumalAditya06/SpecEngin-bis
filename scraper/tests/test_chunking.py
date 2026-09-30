import copy

from scraper.chunking import ChunkConfig, RegexTokenizer, chunk_document, split_text, validate_chunks


def node(kind, text, *, number=None, identifier=None, title=None, pages=None, children=None):
    pages = pages or [1]
    result = {
        "type": kind,
        "title": title,
        "text": text,
        "pages": pages,
        "start_page": min(pages),
        "end_page": max(pages),
        "page_segments": [{"page_number": page, "text": text if index == 0 else ""} for index, page in enumerate(pages)],
        "children": children or [],
    }
    if number is not None:
        result["number"] = number
    if identifier is not None:
        result["identifier"] = identifier
    return result


def document(structure, pages=None):
    pages = pages or [{"page_number": 1, "raw_text": "raw", "cleaned_text": "clean"}]
    return {
        "document_id": "doc-1", "standard_numbers": ["IS 1:2025"], "title": "Manual",
        "document_type": "product_manual", "organization": "BIS", "source_url": "https://example.test/a.pdf",
        "source_file": "data/raw/a.pdf", "sha256": "abc", "metadata_conflict": False,
        "verification_status": "verified", "pages": pages, "structure": structure,
        "_structured_input_file": "structured/a.json", "_structured_input_sha256": "structured-hash",
    }


def test_small_clause_stays_one_chunk():
    doc = document([node("section", "4 REQUIREMENTS", number="4", children=[
        node("clause", "4.1 General\nShort complete requirement.", number="4.1", title="General")
    ])])
    chunks, _ = chunk_document(doc, ChunkConfig(target_tokens=100, soft_max_tokens=120, hard_max_tokens=150, overlap_tokens=10))
    clause = next(chunk for chunk in chunks if chunk["clause_number"] == "4.1")
    assert clause["chunk_count"] == 1
    assert clause["text"] == "4.1 General\nShort complete requirement."


def test_normal_clause_preserves_title_and_provenance():
    doc = document([node("section", "4 REQUIREMENTS", number="4", title="Requirements", children=[
        node("clause", "4.1 General\nThe appliance shall comply.", number="4.1", title="General")
    ])])
    chunks, _ = chunk_document(doc, ChunkConfig())
    clause = next(chunk for chunk in chunks if chunk["clause_number"] == "4.1")
    assert clause["section_number"] == "4"
    assert clause["clause_title"] == "General"
    assert clause["source_url"] and clause["source_file"] and clause["sha256"]


def test_large_clause_splits_below_hard_max_with_overlap():
    text = "4.2 Construction\n" + "\n".join(f"Sentence {index} contains a requirement." for index in range(100))
    doc = document([node("section", "4 REQUIREMENTS", number="4", children=[
        node("clause", text, number="4.2", title="Construction")
    ])])
    config = ChunkConfig(target_tokens=60, soft_max_tokens=80, hard_max_tokens=100, overlap_tokens=8)
    chunks, stats = chunk_document(doc, config)
    clause_chunks = [chunk for chunk in chunks if chunk["clause_number"] == "4.2"]
    assert len(clause_chunks) > 1
    assert all(chunk["token_count"] <= 100 for chunk in clause_chunks)
    assert all(chunk["clause_number"] == "4.2" for chunk in clause_chunks)
    assert stats["clauses_split"] == 1


def test_cross_page_clause_retains_both_pages():
    text = "4.2 Construction\nPage one requirement.\nPage two continuation."
    clause = node("clause", text, number="4.2", pages=[7, 8])
    clause["page_segments"] = [
        {"page_number": 7, "text": "4.2 Construction\nPage one requirement."},
        {"page_number": 8, "text": "Page two continuation."},
    ]
    chunks, _ = chunk_document(document([clause], pages=[
        {"page_number": 7, "raw_text": "", "cleaned_text": ""},
        {"page_number": 8, "raw_text": "", "cleaned_text": ""},
    ]), ChunkConfig())
    assert chunks[0]["pages"] == [7, 8]


def test_nested_subclause_inherits_parent_and_annex():
    subclause = node("subclause", "1.3.1 Definition text", number="1.3.1", title="Definition")
    parent = node("subclause", "1.3 Control unit", number="1.3", children=[subclause])
    clause = node("clause", "1 Quality plan", number="1", children=[parent])
    annex = node("annex", "ANNEX C\nScheme", identifier="C", children=[clause])
    chunks, _ = chunk_document(document([annex]), ChunkConfig())
    nested = next(chunk for chunk in chunks if chunk["clause_number"] == "1.3.1")
    assert nested["parent_clause_number"] == "1.3"
    assert nested["annex_identifier"] == "C"
    assert "Annex C" in nested["context_prefix"]


def test_table_is_kept_as_table_chunks_with_identifier():
    table = node("table", "TABLE 1\nClause | Requirement\n4 | Capacity", identifier="1")
    doc = document([node("annex", "ANNEX D", identifier="D", children=[table])])
    chunks, stats = chunk_document(doc, ChunkConfig())
    table_chunk = next(chunk for chunk in chunks if chunk["structural_type"] == "table")
    assert table_chunk["table_identifier"] == "1"
    assert "Clause | Requirement" in table_chunk["text"]
    assert "Clause | Requirement" in table_chunk["table_header_context"]
    assert stats["tables_chunked"] == 1


def test_chunk_ids_are_stable_across_runs():
    doc = document([node("clause", "4.1 Stable content", number="4.1")])
    first, _ = chunk_document(copy.deepcopy(doc), ChunkConfig())
    second, _ = chunk_document(copy.deepcopy(doc), ChunkConfig())
    assert [chunk["chunk_id"] for chunk in first] == [chunk["chunk_id"] for chunk in second]


def test_metadata_conflict_is_preserved():
    doc = document([node("clause", "4.1 Content", number="4.1")])
    doc["metadata_conflict"] = True
    doc["conflict_type"] = "standard_number_mismatch"
    chunks, _ = chunk_document(doc, ChunkConfig())
    assert chunks[0]["metadata_conflict"] is True
    assert chunks[0]["conflict_type"] == "standard_number_mismatch"


def test_validation_checks_pages_ids_tables_and_provenance():
    table = node("table", "TABLE 1\n1 | value", identifier="1")
    doc = document([table])
    chunks, _ = chunk_document(doc, ChunkConfig())
    checks = validate_chunks(doc, chunks, ChunkConfig())
    assert checks["passed"]


def test_split_text_returns_exact_source_substrings():
    text = "\n".join(f"Line {index} has words and punctuation." for index in range(40))
    pieces = split_text(text, RegexTokenizer(), ChunkConfig(target_tokens=30, soft_max_tokens=40, hard_max_tokens=50, overlap_tokens=5))
    assert len(pieces) > 1
    assert all(piece["text"] == text[piece["start"]:piece["end"]] for piece in pieces)


def test_heading_only_parent_is_context_not_tiny_standalone_chunk():
    child = node("clause", "1.1 Substantive requirement text.", number="1.1", title="Requirement")
    parent = node("clause", "1. QUALITY ASSURANCE PLAN", number="1", title="QUALITY ASSURANCE PLAN", children=[child])
    chunks, _ = chunk_document(document([parent]), ChunkConfig())
    assert not any(chunk["text"] == parent["text"] for chunk in chunks)
    assert any(chunk["parent_clause_number"] == "1" for chunk in chunks)


def test_unknown_optional_metadata_is_omitted_not_invented():
    chunks, _ = chunk_document(document([node("front_matter", "Useful front matter text.")]), ChunkConfig())
    assert "clause_number" not in chunks[0]
    assert "annex_identifier" not in chunks[0]
    assert "table_identifier" not in chunks[0]
