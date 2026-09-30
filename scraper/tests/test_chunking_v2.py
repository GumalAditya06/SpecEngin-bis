"""Stage 3.1A tests: tokenizer-aware chunk realignment.

The splitting, provenance, and validation logic is exercised against a stub
WordPiece tokenizer that implements the same protocol as the real one, so the
suite is deterministic, fast, and needs no model download. A small set of
``real_model`` tests then confirms the stub's assumptions actually hold for
``BAAI/bge-base-en-v1.5`` at the pinned revision.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from scraper.chunking import (
    DEFAULT_EMBEDDING_MAX_TOKENS,
    BgeTokenizer,
    ChunkConfig,
    RegexTokenizer,
    build_tokenizer,
    chunk_document,
    resolve_embedding_max_tokens,
    split_text,
    split_text_for_embedding,
    unit_signature,
)
from scraper.chunking_v2 import (
    LEGACY_LIMITS,
    audit_chunks,
    run_chunking_v2,
    unit_source_coverage,
    validate_v2,
)
from scraper.embeddings import embedding_prefix


# ---------------------------------------------------------------------------
# Stub tokenizer
# ---------------------------------------------------------------------------


class StubWordPiece:
    """A deterministic stand-in for the BGE WordPiece tokenizer.

    Words are split into ``pieces_per_word`` sub-tokens, so text costs more
    tokens here than under ``regex_v1`` — the same direction and rough magnitude
    as the real undercount that motivated Stage 3.1A. Character spans are
    returned so ``cutoff`` and ``overlap_start`` behave like the real thing.
    """

    counts_embedding_input = True
    name = "stub_bge"

    def __init__(self, pieces_per_word: int = 2, special_token_count: int = 2,
                 model_max_length: int = 512):
        self.pieces_per_word = pieces_per_word
        self._special_token_count = special_token_count
        self.model_max_length = model_max_length

    @property
    def special_token_count(self) -> int:
        return self._special_token_count

    @property
    def content_budget(self) -> int:
        return self.model_max_length - self._special_token_count

    def describe(self) -> dict:
        return {
            "tokenizer": self.name,
            "tokenizer_class": type(self).__name__,
            "model": "stub-wordpiece",
            "model_revision": "stub",
            "provider": "stub",
            "model_max_length": self.model_max_length,
            "special_tokens": ["[CLS]", "[SEP]"][:self._special_token_count],
            "special_token_count": self._special_token_count,
            "usable_content_token_budget": self.content_budget,
        }

    def _spans(self, text: str) -> list[tuple[int, int]]:
        spans: list[tuple[int, int]] = []
        for match in re.finditer(r"\w+|[^\w\s]", text, re.UNICODE):
            start, end = match.span()
            span = max(1, end - start)
            for index in range(self.pieces_per_word):
                left = start + span * index // self.pieces_per_word
                right = start + span * (index + 1) // self.pieces_per_word
                if right > left:
                    spans.append((left, right))
        return spans

    def count(self, text: str) -> int:
        return len(self._spans(text)) if text else 0

    def input_count(self, text: str, prefix: str = "") -> int:
        head = str(prefix or "").strip()
        body = str(text or "").strip()
        combined = f"{head}\n\n{body}" if head else body
        return (self.count(combined) if combined else 0) + self._special_token_count

    def prefix_cost(self, prefix: str) -> int:
        head = str(prefix or "").strip()
        if not head:
            return self._special_token_count
        return self.count(f"{head}\n\n") + self._special_token_count

    def body_budget(self, prefix: str, max_input_tokens: int) -> int:
        return max(0, max_input_tokens - self.prefix_cost(prefix))

    def cutoff(self, text: str, maximum: int) -> int:
        spans = self._spans(text)
        if len(spans) <= maximum:
            return len(text)
        if maximum < 1:
            return 0
        end = spans[maximum - 1][1]
        stripped = text[:end].rstrip()
        return len(stripped) if stripped else end

    def overlap_start(self, text: str, end: int, overlap: int) -> int:
        if overlap <= 0:
            return end
        spans = [span for span in self._spans(text[:end]) if span[0] < end]
        return 0 if len(spans) <= overlap else spans[-overlap][0]


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def with_pages(node: dict, first_page: int = 3) -> dict:
    """Attach page segments so page provenance is computable for a node."""
    text = node.get("text", "")
    midpoint = len(text) // 2
    cut = text.find("\n", midpoint)
    cut = cut if cut > 0 else midpoint
    return {**node, "page_segments": [
        {"text": text[:cut], "page_number": first_page},
        {"text": text[cut:], "page_number": first_page + 1},
    ]}


def make_document(document_id: str = "doc-1", nodes=None) -> dict:
    nodes = nodes if nodes is not None else [with_pages({
        "type": "clause", "number": "4.2", "title": "Control Unit Requirements",
        "text": "The control unit shall comply with clause 4.3 of this standard. "
                "It shall be constructed so that it cannot be defeated by the user.\n\n"
                "The unit shall be tested at the ambient conditions stated in Table 1. "
                "Test equipment shall be calibrated before each verification run.\n",
        "children": [],
    })]
    # The document's page list must be exactly the pages its nodes reference,
    # because chunking validates that every source page is represented.
    seen: dict[int, None] = {}
    for node in nodes:
        for segment in node.get("page_segments") or []:
            seen.setdefault(segment["page_number"], None)
    return {
        "document_id": document_id,
        "title": "Electric Food Mixer - Product Manual",
        "standard_numbers": ["IS 2347:2023"],
        "document_type": "product_manual",
        "organization": "Bureau of Indian Standards",
        "source_url": f"https://example.invalid/{document_id}.pdf",
        "source_file": f"{document_id}.pdf",
        "sha256": "a" * 64,
        "pages": [{"page_number": n} for n in seen],
        "structure": nodes,
    }


LONG_CLAUSE = (
    "The appliance shall be so designed that the control unit operates correctly under the "
    "conditions given in this standard. This requirement applies to every model covered by "
    "the scope of this clause. The manufacturer shall demonstrate conformity by appropriate "
    "test evidence. The test shall be carried out at the rated voltage and frequency. Any "
    "deviation shall be recorded and justified in the test report. The test laboratory shall "
    "retain the sample for the period specified in the quality plan. The test report shall "
    "clearly identify the model and its rating. Verification shall be repeated whenever a "
    "significant change is made to the design. The evidence shall be available for inspection. "
    "The requirement forms part of the mandatory specification for this product category.\n\n"
    "A second paragraph begins here and carries its own sentence structure. It must remain "
    "readable as a whole unit once the clause has been divided across several chunks. The "
    "manufacturer is required to state the sampling plan used during verification. Records "
    "shall be kept for the statutory period and made available to the inspecting authority. "
    "Nothing in this paragraph may be dropped in order to satisfy a token budget.\n"
)

LONG_TABLE = "TABLE 1\n\n(1)\n(2)\n(3)\nTest Details\nTest\n" + (
    "equipment\nrequirement\nR: required\nLevels of Control\n"
) + "\n".join(
    f"Item {n}\nCl. {n}\nIS 302-2-15\nR\nOne\nOnce in a month for each type" for n in range(1, 60)
)


@pytest.fixture
def tokenizer() -> StubWordPiece:
    return StubWordPiece()


@pytest.fixture
def bge_config() -> ChunkConfig:
    return ChunkConfig(tokenizer="bge", embedding_max_tokens=200, overlap_tokens=20)


# ---------------------------------------------------------------------------
# 1. Actual BGE tokenizer counting
# ---------------------------------------------------------------------------


def test_bge_tokenizer_counts_with_the_real_tokenizer():
    """The tokenizer wrapper must agree with the underlying model tokenizer."""
    pytest.importorskip("transformers")
    from transformers import AutoTokenizer

    from scraper.config import resolve_embedding_spec

    try:
        real = BgeTokenizer(resolve_embedding_spec())
    except Exception as exc:  # pragma: no cover - offline guard
        pytest.skip(f"BGE tokenizer unavailable offline: {exc}")
    reference = AutoTokenizer.from_pretrained(
        resolve_embedding_spec().model_name, revision=resolve_embedding_spec().revision
    )
    samples = [
        "Control unit requirements.",
        "TABLE 1\n\nItem 1\nCl. 2\nIS 302-2-15\nR\nOne",
        "IS 4250:2025 | Annex C — List Of Test Equipment | Clause 1.3.1\n\nBody text here.",
    ]
    for text in samples:
        assert real.count(text) == len(reference.encode(text, add_special_tokens=False))


def test_bge_count_is_not_the_regex_count():
    """Counting with the wrong tokenizer is the bug this stage exists to remove."""
    wordpiece, regex = StubWordPiece(pieces_per_word=2), RegexTokenizer()
    text = LONG_CLAUSE
    assert wordpiece.count(text) > regex.count(text)
    ratio = wordpiece.count(text) / regex.count(text)
    assert 1.0 < ratio < 3.0


def test_counting_is_additive_across_the_prefix_boundary():
    """The splitter budgets body tokens as limit minus prefix cost, which needs
    tokenization to be additive at the seam."""
    tokenizer = StubWordPiece()
    prefix = "IS 4250:2025 | Annex C | Clause 1.3.1"
    body = "The control unit shall comply with this requirement."
    combined = f"{prefix}\n\n{body}"
    assert tokenizer.count(combined) == tokenizer.count(f"{prefix}\n\n") + tokenizer.count(body)
    assert tokenizer.input_count(body, prefix) == tokenizer.count(combined) + tokenizer.special_token_count


# ---------------------------------------------------------------------------
# 2. Special-token handling
# ---------------------------------------------------------------------------


def test_special_tokens_are_measured_not_assumed(tokenizer):
    body = "short body"
    assert tokenizer.input_count(body) == tokenizer.count(body) + 2
    assert tokenizer.input_count(body) > tokenizer.count(body)
    # The wrapper reports the overhead it actually observed.
    assert tokenizer.special_token_count == 2
    assert tokenizer.content_budget == tokenizer.model_max_length - 2


@pytest.mark.real_model
def test_bge_special_tokens_are_cls_and_sep():
    from scraper.config import resolve_embedding_spec

    try:
        real = BgeTokenizer(resolve_embedding_spec())
    except Exception as exc:  # pragma: no cover - offline guard
        pytest.skip(f"BGE tokenizer unavailable offline: {exc}")
    assert real.model_max_length == 512
    assert real.special_token_count == 2
    assert set(real.describe()["special_tokens"]) >= {"[CLS]", "[SEP]"}
    assert real.content_budget == 510


def test_empty_prefix_costs_only_the_special_tokens(tokenizer):
    assert tokenizer.prefix_cost("") == tokenizer.special_token_count
    assert tokenizer.body_budget("", 200) == 200 - tokenizer.special_token_count


# ---------------------------------------------------------------------------
# 3. context_prefix token counting
# ---------------------------------------------------------------------------


def test_prefix_consumes_body_budget(tokenizer):
    short, long = "IS 4250:2025 | Annex C | Clause 1.3.1", (
        "IS 4250:2025 | Annex C — Comprehensive List Of Test Equipment "
        "For Electric Food Mixers And Similar Appliances | Clause 1.3.1 Control Unit"
    )
    assert tokenizer.body_budget(short, 200) > tokenizer.body_budget(long, 200)


def test_limit_applies_to_prefix_plus_text_not_text_alone(tokenizer, bge_config):
    """A body that fits on its own can still overflow once the prefix is added."""
    body = "word " * 90
    prefix = "IS 4250:2025 | Annex C | Clause 1.3.1"
    assert tokenizer.count(body) <= bge_config.embedding_max_tokens
    assert tokenizer.input_count(body, prefix) > bge_config.embedding_max_tokens
    pieces = split_text_for_embedding(body, tokenizer, bge_config, prefix)
    assert len(pieces) > 1
    for piece in pieces:
        assert tokenizer.input_count(piece["text"], prefix) <= bge_config.embedding_max_tokens


def test_table_header_is_part_of_the_measured_input(tokenizer, bge_config):
    header = "TABLE 1\n\n(1)\n(2)\n(3)\nTest Details\nTest\nequipment\nrequirement"
    body = "9\nLeakage current\n13\nIS 302-2-15\nR\nOne\nOnce in a week\n"
    chunk = {"context_prefix": "IS 367:1993 | Annex C", "table_header_context": header, "text": body}
    prefix = embedding_prefix(chunk)
    assert header in prefix
    assert prefix.startswith(header)
    # The chunker must budget against the same string the embedder will build.
    assert tokenizer.input_count(body, prefix) == tokenizer.count(f"{prefix}\n\n{body}") + 2


# ---------------------------------------------------------------------------
# 4. Safe token limit
# ---------------------------------------------------------------------------


def test_safe_limit_default_is_four_hundred_fifty():
    assert DEFAULT_EMBEDDING_MAX_TOKENS == 450
    # 512 positions - 2 special tokens - 56 prefix tokens = 454 usable.
    assert 512 - 2 - 56 >= DEFAULT_EMBEDDING_MAX_TOKENS


def test_safe_limit_is_configurable(monkeypatch):
    assert resolve_embedding_max_tokens(None) == 450
    assert resolve_embedding_max_tokens(300) == 300
    monkeypatch.setenv("EMBEDDING_MAX_TOKENS", "380")
    assert resolve_embedding_max_tokens(None) == 380
    assert resolve_embedding_max_tokens(300) == 300  # explicit argument wins
    monkeypatch.delenv("EMBEDDING_MAX_TOKENS")
    assert resolve_embedding_max_tokens(None) == 450


def test_reported_limit_is_never_exceeded(tokenizer, bge_config):
    document = make_document(nodes=[{"type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": []}])
    chunks, _ = chunk_document(document, bge_config)
    assert len(chunks) > 1
    for chunk in chunks:
        assert chunk["embedding_input_tokens"] <= bge_config.embedding_max_tokens
        assert chunk["embedding_max_tokens"] == bge_config.embedding_max_tokens


def test_chunks_under_the_limit_are_left_alone(tokenizer, bge_config):
    document = make_document()
    chunks, _ = chunk_document(document, bge_config)
    assert len(chunks) == 1
    assert chunks[0]["chunk_count"] == 1
    assert chunks[0]["chunk_index"] == 0


def test_unsupported_tokenizer_name_is_rejected():
    with pytest.raises(ValueError, match="available: regex_v1, bge"):
        build_tokenizer("wordpiece_v9", use_cache=False)


# ---------------------------------------------------------------------------
# 5. Oversized clause splitting
# ---------------------------------------------------------------------------


def test_oversized_clause_is_split_with_provenance(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document(nodes=[with_pages({
        "type": "clause", "number": "4.2", "title": "Control Unit",
        "text": LONG_CLAUSE, "children": [],
    })])
    chunks, stats = chunk_document(document, bge_config)
    assert len(chunks) > 1
    assert stats["structural_units_split"] == 1
    assert stats["clauses_split"] == 1
    # Every piece keeps the parent's identity.
    assert {chunk["clause_number"] for chunk in chunks} == {"4.2"}
    assert {chunk["document_id"] for chunk in chunks} == {"doc-1"}
    assert {chunk["source_url"] for chunk in chunks} == {document["source_url"]}
    assert {chunk["sha256"] for chunk in chunks} == {document["sha256"]}
    assert all(chunk["pages"] for chunk in chunks)
    assert [chunk["chunk_index"] for chunk in chunks] == list(range(len(chunks)))
    assert {chunk["chunk_count"] for chunk in chunks} == {len(chunks)}


def test_split_pieces_tile_the_source_exactly(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document(nodes=[with_pages({
        "type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": [],
    })])
    chunks, _ = chunk_document(document, bge_config)
    assert unit_source_coverage(chunks[0]["unit_text_length"], chunks)
    for chunk in chunks:
        assert LONG_CLAUSE[chunk["unit_text_start"]:chunk["unit_text_end"]] == chunk["text"]


def test_split_makes_progress_and_covers_a_huge_single_paragraph(tokenizer, bge_config):
    body = "supercalifragilistic " * 400
    pieces = split_text_for_embedding(body, tokenizer, bge_config, "IS 367:1993")
    assert len(pieces) > 1
    for piece in pieces:
        assert tokenizer.input_count(piece["text"], "IS 367:1993") <= bge_config.embedding_max_tokens
    assert unit_source_coverage(len(body), pieces)


# ---------------------------------------------------------------------------
# 6. Sentence-boundary splitting
# ---------------------------------------------------------------------------


def test_splits_never_land_inside_a_sentence(tokenizer, bge_config):
    pieces = split_text_for_embedding(LONG_CLAUSE, tokenizer, bge_config, "IS 2347:2023 | Cl. 4.2")
    assert len(pieces) > 1
    for piece in pieces:
        stripped = piece["text"].strip()
        # A piece must not *end* on a fragment: it ends at a paragraph, a
        # sentence, or a line, never on a dangling connective.
        assert not re.search(r"\b(in|of|the|and|shall|to|for|with|be|is)$", stripped), stripped[-40:]
    # Pieces begin at a boundary too, apart from the deliberate overlap, which
    # walks the start backwards on purpose to carry context forward.
    for piece in pieces[1:]:
        if piece["overlap_tokens"] == 0:
            assert piece["text"][0] in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789(\n"


def test_prefers_paragraph_boundaries_before_sentences(tokenizer):
    prefix = "IS 2347:2023 | Cl. 4.2"
    first_paragraph = LONG_CLAUSE.split("\n\n")[0]
    # Leave room for exactly the first paragraph plus the prefix and specials.
    limit = tokenizer.count(first_paragraph) + tokenizer.prefix_cost(prefix) + 2
    config = ChunkConfig(tokenizer="bge", embedding_max_tokens=limit, overlap_tokens=10)
    pieces = split_text_for_embedding(LONG_CLAUSE, tokenizer, config, prefix)
    assert len(pieces) >= 2
    # The paragraph break is the first candidate that fits, so the splitter
    # takes it rather than stopping mid-sentence earlier in the paragraph.
    assert pieces[0]["end"] == LONG_CLAUSE.index("\n\n") + 2


def test_snap_to_break_never_cuts_inside_a_word():
    """WordPiece token edges fall mid-word; boundaries must move to real breaks."""
    from scraper.chunking import _snap_to_break

    text = "alpha beta gamma"
    assert _snap_to_break(text, 7) == 6      # inside "beta" -> back to the space
    assert _snap_to_break(text, 5) == 5       # no break available, keep the raw edge
    assert _snap_to_break(text, 6) == 6       # trailing space is trimmed
    assert _snap_to_break(text, 0) == 0
    assert _snap_to_break(text, 17) == 11     # back to the start of "gamma"
    # With no break available, the raw offset is returned so the split proceeds.
    assert _snap_to_break("unbreakableword", 8) == 8


def test_cutoff_snaps_to_a_safe_break():
    """The tokenizer's cutoff must land on a break, not a raw token edge."""
    from scraper.chunking import _snap_to_break

    class Snapping(StubWordPiece):
        def cutoff(self, text, maximum):
            spans = self._spans(text)
            return _snap_to_break(text, len(text) if len(spans) <= maximum else spans[maximum - 1][1])

    tokenizer = Snapping()
    text = "alpha beta gamma delta epsilon zeta"
    assert text[:tokenizer.cutoff(text, 4)].rstrip() == "alpha"
    assert text[:tokenizer.cutoff(text, 8)].rstrip() == "alpha beta gamma"


def test_cutoff_still_makes_progress_on_an_unbreakable_run():
    """A single run longer than the budget must split rather than refuse."""
    fine = StubWordPiece(pieces_per_word=40)
    unbreakable = "abcdefghij" * 30
    offset = fine.cutoff(unbreakable, 20)
    assert 0 < offset < len(unbreakable)


def test_overlap_is_bounded_by_the_budget(tokenizer, bge_config):
    pieces = split_text_for_embedding(LONG_CLAUSE, tokenizer, bge_config, "IS 2347:2023")
    assert len(pieces) > 1
    assert all(piece["overlap_tokens"] < bge_config.embedding_max_tokens for piece in pieces)
    assert pieces[0]["overlap_tokens"] == 0


# ---------------------------------------------------------------------------
# 7. Provenance preservation
# ---------------------------------------------------------------------------


def test_provenance_survives_the_boundary_change(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document(nodes=[with_pages({
        "type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": [],
    })])
    chunks, _ = chunk_document(document, bge_config)
    # Every document-level identifier is carried through to every piece.
    for document_key, chunk_key in (
        ("document_id", "document_id"), ("title", "document_title"),
        ("document_type", "document_type"), ("organization", "organization"),
        ("sha256", "sha256"), ("source_url", "source_url"), ("source_file", "source_file"),
    ):
        assert {chunk[chunk_key] for chunk in chunks} == {document[document_key]}, chunk_key
    for chunk in chunks:
        assert chunk["standard_numbers"] == document["standard_numbers"]
    assert {chunk["clause_number"] for chunk in chunks} == {"4.2"}
    for chunk in chunks:
        assert chunk["pages"], "page provenance must survive the split"
        assert chunk["start_page"] == min(chunk["pages"])
        assert chunk["end_page"] == max(chunk["pages"])


def test_pages_reflect_the_offset_of_each_piece(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    # Split the clause across two pages so page attribution has to be computed
    # from each piece's character offset.
    first = LONG_CLAUSE[:1000]
    document = make_document(nodes=[{
        "type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": [],
        "page_segments": [{"text": first, "page_number": 3},
                          {"text": LONG_CLAUSE[1000:], "page_number": 4}],
    }])
    chunks, _ = chunk_document(document, bge_config)
    assert len(chunks) > 1
    assert all(set(chunk["pages"]) <= {3, 4} for chunk in chunks)
    assert all(chunk["pages"] for chunk in chunks)
    # The first piece sits early in the clause, so it cannot be on page 4.
    assert 3 in chunks[0]["pages"]
    assert chunks[-1]["pages"][-1] == 4


def test_source_text_is_never_rewritten(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document(nodes=[{"type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": []}])
    chunks, _ = chunk_document(document, bge_config)
    for chunk in chunks:
        assert chunk["text"] in LONG_CLAUSE
        # The stored text is the source slice, never the derived embedding input.
        assert "\n\n" not in chunk["text"][:200] or "IS 2347" not in chunk["text"]


# ---------------------------------------------------------------------------
# 8. Table splitting
# ---------------------------------------------------------------------------


def test_table_split_keeps_headers_and_row_provenance(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document(nodes=[{
        "type": "table", "identifier": "1", "title": "Test Equipment",
        "text": LONG_TABLE, "children": [],
    }])
    chunks, stats = chunk_document(document, bge_config)
    assert len(chunks) > 1
    assert stats["tables_chunked"] == 1
    for chunk in chunks:
        assert chunk["structural_type"] == "table"
        assert chunk["table_identifier"] == "1"
        assert chunk["table_header_context"], "each table piece must carry the column headings"
        assert chunk["text"] in LONG_TABLE
    assert unit_source_coverage(len(LONG_TABLE), chunks)


def test_table_header_is_reserved_within_the_limit(tokenizer, bge_config, monkeypatch):
    """A piece plus its header must still fit; the header is not free."""
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    config = ChunkConfig(tokenizer="bge", embedding_max_tokens=200, overlap_tokens=20,
                         table_header_max_tokens=60)
    document = make_document(nodes=[{
        "type": "table", "identifier": "1", "text": LONG_TABLE, "children": [],
    }])
    chunks, _ = chunk_document(document, config)
    assert all(chunk["embedding_input_tokens"] <= 200 for chunk in chunks)
    header_tokens = tokenizer.count(chunks[0]["table_header_context"])
    assert header_tokens <= config.table_header_max_tokens


def test_no_table_row_content_is_lost(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document(nodes=[{
        "type": "table", "identifier": "1", "text": LONG_TABLE, "children": [],
    }])
    chunks, _ = chunk_document(document, bge_config)
    # Reassemble the source from the recorded offsets, skipping the deliberate
    # overlap, and require it to equal the original byte for byte.
    restored, cursor = [], 0
    for chunk in chunks:
        start, end = chunk["unit_text_start"], chunk["unit_text_end"]
        restored.append(LONG_TABLE[max(cursor, start):end])
        cursor = max(cursor, end)
    restored.append(LONG_TABLE[cursor:])
    assert "".join(restored) == LONG_TABLE
    # First, middle, and last rows must each be present somewhere.
    for item in ("Item 1", "Item 30", "Item 59"):
        assert any(item in chunk["text"] for chunk in chunks), item


# ---------------------------------------------------------------------------
# 9. Deterministic chunk IDs
# ---------------------------------------------------------------------------


def test_split_ids_are_deterministic(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document(nodes=[{"type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": []}])
    first, _ = chunk_document(document, bge_config)
    second, _ = chunk_document(document, bge_config)
    assert [c["chunk_id"] for c in first] == [c["chunk_id"] for c in second]
    assert len({c["chunk_id"] for c in first}) == len(first)


def test_ids_are_not_random_uuids(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document(nodes=[{"type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": []}])
    chunks, _ = chunk_document(document, bge_config)
    for chunk in chunks:
        assert re.fullmatch(r"[0-9a-f]{24}", chunk["chunk_id"]), chunk["chunk_id"]


def test_identical_content_in_two_documents_gets_distinct_ids(tokenizer, bge_config, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    nodes = [{"type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": []}]
    a, _ = chunk_document(make_document("doc-a", nodes), bge_config)
    b, _ = chunk_document(make_document("doc-b", nodes), bge_config)
    assert {c["chunk_id"] for c in a}.isdisjoint({c["chunk_id"] for c in b})


def test_a_reused_id_never_gets_different_text(tokenizer, bge_config, monkeypatch):
    """Split ids are content-derived, so a changed boundary cannot collide."""
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    nodes = [{"type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": []}]
    before, _ = chunk_document(make_document("doc-1", nodes), bge_config)
    tighter = ChunkConfig(tokenizer="bge", embedding_max_tokens=120, overlap_tokens=20)
    after, _ = chunk_document(make_document("doc-1", nodes), tighter)
    shared = {c["chunk_id"] for c in before} & {c["chunk_id"] for c in after}
    texts = {c["chunk_id"]: c["text"] for c in before + after}
    assert all(len({texts[cid] for cid in [cid]}) == 1 for cid in shared)  # sanity
    by_id = {}
    for chunk in before + after:
        by_id.setdefault(chunk["chunk_id"], set()).add(chunk["text"])
    assert all(len(variants) == 1 for variants in by_id.values())


def test_unit_signature_is_independent_of_walk_position():
    node = {"type": "clause", "number": "4.2", "title": "Control Unit", "text": "body text"}
    assert unit_signature("d1", node, [], "body text") == unit_signature("d1", node, [], "body text")
    assert unit_signature("d1", node, [], "body text") != unit_signature("d2", node, [], "body text")
    assert unit_signature("d1", node, [], "body text") != unit_signature("d1", node, [], "other text")


# ---------------------------------------------------------------------------
# 10. Zero truncation
# ---------------------------------------------------------------------------


def test_no_piece_can_reach_the_model_window(tokenizer):
    """Even a chunk at the safe limit leaves the model's window untouched."""
    config = ChunkConfig(tokenizer="bge", embedding_max_tokens=500, overlap_tokens=20)
    pieces = split_text_for_embedding(LONG_CLAUSE * 3, tokenizer, config, "IS 4250:2025 | Annex C")
    assert len(pieces) > 1
    for piece in pieces:
        assert tokenizer.input_count(piece["text"], "IS 4250:2025 | Annex C") <= tokenizer.model_max_length


def test_audit_flags_exactly_the_chunks_that_would_truncate(tokenizer, bge_config):
    document = make_document(nodes=[
        {"type": "clause", "number": "1", "text": "short clause body", "children": []},
        {"type": "clause", "number": "2", "text": LONG_CLAUSE, "children": []},
    ])
    chunks, _ = chunk_document(document, ChunkConfig(tokenizer="bge", embedding_max_tokens=10000))
    audit = audit_chunks(chunks, tokenizer, bge_config)
    assert audit["chunks_audited"] == 2
    assert audit["chunks_exceeding_safe_limit"] == 1
    assert audit["chunks_that_would_truncate"] == 0  # 200-token body, 512 window
    flagged = [row for row in audit["chunks"] if row["exceeds_safe_limit"]]
    assert flagged[0]["action_required"] == "split"
    assert flagged[0]["bge_token_count"] > flagged[0]["regex_token_count"]


def test_audit_records_both_token_counts(tokenizer, bge_config):
    document = make_document(nodes=[{"type": "clause", "number": "2", "text": LONG_CLAUSE, "children": []}])
    chunks, _ = chunk_document(document, ChunkConfig(tokenizer="bge", embedding_max_tokens=10000))
    row = audit_chunks(chunks, tokenizer, bge_config)["chunks"][0]
    for key in ("regex_token_count", "bge_token_count", "context_prefix_token_count",
                "embedding_input_tokens", "model_max_length", "safe_limit", "exceeds_safe_limit",
                "action_required", "clause_number", "pages", "document_id"):
        assert key in row, key


def test_coverage_check_detects_a_gap():
    pieces = [
        {"unit_text_start": 0, "unit_text_end": 10},
        {"unit_text_start": 20, "unit_text_end": 30},
    ]
    assert not unit_source_coverage(30, pieces)
    assert unit_source_coverage(30, [pieces[0], {"unit_text_start": 10, "unit_text_end": 30}])
    assert not unit_source_coverage(30, [])


# ---------------------------------------------------------------------------
# 11. Unchanged chunks remain unchanged
# ---------------------------------------------------------------------------


def test_regex_v1_path_is_untouched_by_this_stage(monkeypatch):
    """Stage 2.5 behaviour, including its identifiers, must be byte-identical.

    The expected id is pinned as a literal: if a future change to the id scheme
    or the unit walk shifts regex_v1 output, the Stage 2.5 corpus on disk would
    no longer be reproducible and this test is the thing that catches it.
    """
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: RegexTokenizer())
    document = make_document(nodes=[{"type": "clause", "number": "4.2", "title": "Control Unit",
                                    "text": LONG_CLAUSE, "children": []}])
    chunks, _ = chunk_document(document, ChunkConfig(tokenizer="regex_v1", **LEGACY_LIMITS))
    assert [chunk["chunk_id"] for chunk in chunks] == ["70c9fe839779d2283ba2b81e"]
    # No embedding-specific field leaks into the regex_v1 output.
    assert "embedding_input_tokens" not in chunks[0]
    assert "embedding_max_tokens" not in chunks[0]
    # Pure provenance was added; it changes no behaviour.
    assert chunks[0]["unit_text_start"] == 0
    assert chunks[0]["unit_text_length"] == len(LONG_CLAUSE)
    assert "text" in chunks[0]


def test_unchanged_unit_keeps_its_stage_2_5_id(tokenizer, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document()
    chunks, _ = chunk_document(document, ChunkConfig(tokenizer="bge", embedding_max_tokens=200))
    legacy, _ = chunk_document(document, ChunkConfig(tokenizer="regex_v1", **LEGACY_LIMITS))
    assert len(chunks) == len(legacy) == 1
    assert chunks[0]["chunk_id"] == legacy[0]["chunk_id"]


def test_short_units_are_not_split_just_because_the_limit_is_small(tokenizer, monkeypatch):
    monkeypatch.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    document = make_document()
    for limit in (200, 400, 450):
        chunks, _ = chunk_document(document, ChunkConfig(tokenizer="bge", embedding_max_tokens=limit))
        assert len(chunks) == 1, f"limit {limit} split a chunk that already fit"


# ---------------------------------------------------------------------------
# 12. End-to-end runner
# ---------------------------------------------------------------------------


def build_corpus(tmp_path: Path) -> Path:
    """A miniature structured corpus with one short and one oversized clause."""
    processed = tmp_path / "processed"
    structured = processed / "structured"
    chunks = processed / "chunks"
    structured.mkdir(parents=True)
    chunks.mkdir(parents=True)
    documents = [
        make_document("doc-a", [with_pages({
            "type": "clause", "number": "1", "title": "Scope",
            "text": "This standard covers electric kettles.", "children": [],
        })]),
        make_document("doc-b", [with_pages({
            "type": "clause", "number": "4.2", "title": "Control Unit",
            "text": LONG_CLAUSE, "children": [],
        })]),
    ]
    for document in documents:
        (structured / f"{document['document_id']}.json").write_text(
            json.dumps(document), encoding="utf-8"
        )
    (structured / "structure_report.json").write_text("{}", encoding="utf-8")
    (chunks / "chunks.jsonl").write_text("", encoding="utf-8")
    return chunks / "chunks.jsonl"


def test_runner_writes_v2_without_touching_stage_2_5(tmp_path, tokenizer, monkeypatch):
    from scraper.config import Settings

    old_path = build_corpus(tmp_path)
    old_path.write_text(
        json.dumps({"chunk_id": "legacy", "document_id": "doc-a", "text": "x",
                    "token_count": 5, "context_prefix": "p"}) + "\n",
        encoding="utf-8",
    )
    before = old_path.read_bytes()
    # The runner resolves its own tokenizer, so both call sites need the stub.
    stub = lambda name, **_: tokenizer  # noqa: E731
    monkeypatch.setattr("scraper.chunking.build_tokenizer", stub)
    monkeypatch.setattr("scraper.chunking_v2.build_tokenizer", stub)

    result = run_chunking_v2(Settings(data_dir=tmp_path), config=ChunkConfig(
        tokenizer="bge", embedding_max_tokens=200, overlap_tokens=20))

    out = tmp_path / "processed" / "chunks_v2"
    assert (out / "chunks.jsonl").exists()
    assert (out / "chunking_report.json").exists()
    assert (out / "comparison_report.json").exists()
    assert old_path.read_bytes() == before, "Stage 2.5 corpus must not be modified"
    assert result["comparison"]["chunks_exceeding_safe_limit_after"] == 0
    assert result["report"]["validation"]["checks"]["passed"] is True


def test_runner_rejects_a_non_bge_config(tmp_path):
    from scraper.config import Settings

    build_corpus(tmp_path)
    with pytest.raises(ValueError, match="requires the BGE tokenizer"):
        run_chunking_v2(Settings(data_dir=tmp_path), config=ChunkConfig(tokenizer="regex_v1"))


def test_runner_requires_the_stage_2_5_corpus(tmp_path):
    from scraper.config import Settings

    (tmp_path / "processed" / "structured").mkdir(parents=True)
    with pytest.raises(FileNotFoundError, match="chunks.jsonl"):
        run_chunking_v2(Settings(data_dir=tmp_path))


def test_validate_v2_catches_a_forged_chunk(tokenizer, bge_config):
    """The validator must fail when a piece is tampered with, not just pass."""
    document = make_document(nodes=[with_pages({
        "type": "clause", "number": "4.2", "text": LONG_CLAUSE, "children": [],
    })])
    document["_structured_input_file"] = "x.json"
    monkey = pytest.MonkeyPatch()
    monkey.setattr("scraper.chunking.build_tokenizer", lambda name, **_: tokenizer)
    try:
        chunks, _ = chunk_document(document, bge_config)
    finally:
        monkey.undo()
    baseline = validate_v2(chunks, [], [document], tokenizer, bge_config)
    assert baseline["checks"]["passed"] is True

    tampered = [dict(chunk) for chunk in chunks]
    tampered[-1] = {**tampered[-1], "text": "rewritten by someone else"}
    broken = validate_v2(tampered, [], [document], tokenizer, bge_config)
    assert broken["checks"]["source_text_preserved"] is False
    assert broken["checks"]["passed"] is False

    dropped = [dict(chunk) for chunk in chunks][1:]
    assert validate_v2(dropped, [], [document], tokenizer, bge_config)["checks"][
        "no_source_content_dropped"
    ] is False

    metadata = [dict(chunk) for chunk in chunks]
    metadata[0] = {**metadata[0], "clause_number": "9.9"}
    assert validate_v2(metadata, [], [document], tokenizer, bge_config)["checks"][
        "clause_metadata_preserved"
    ] is False


def test_split_text_legacy_behaviour_is_unchanged():
    """The Stage 2.5 splitter must not have been altered by the new code path."""
    regex, config = RegexTokenizer(), ChunkConfig(target_tokens=40, soft_max_tokens=60,
                                                 hard_max_tokens=80, overlap_tokens=10)
    pieces = split_text(LONG_CLAUSE, regex, config)
    assert len(pieces) > 1
    assert all(pieces[i]["end"] >= pieces[i + 1]["start"] for i in range(len(pieces) - 1))
    assert pieces[0]["start"] == 0
    assert pieces[-1]["end"] == len(LONG_CLAUSE)
