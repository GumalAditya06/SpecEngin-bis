"""Tests for the grounded RAG pipeline invariants.

Runs on SQLite (see conftest.py) against seeded sample data.
"""

import pytest

from app.core.db import async_session_local, engine
from app.models import Base
from app.models.base import Chunk, Document, Source
from app.rag.assembler import assemble_citations
from app.rag.embeddings import embed_text
from app.rag.pipeline import run_pipeline
from app.rag.response_builder import (
    build_response,
    determine_confidence,
    _guard_is_numbers,
)
from app.rag.reranker import rerank


@pytest.fixture(autouse=True)
async def _tables():
    """Ensure tables exist in the conftest temp SQLite database."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def make_sections(*items: tuple[str, str]) -> list[dict]:
    """Evidence sections: (standard_number, clause, excerpt) tuples."""
    return [
        {
            "standard_number": std,
            "clauses": [{"clause": clause, "excerpt": text, "score": 0.9}],
            "total_excerpts": 1,
        }
        for std, clause, text in items
    ]


def make_citations(sections: list[dict]) -> list[dict]:
    return [
        {
            "source_id": f"src-{i}",
            "document_id": f"doc-{i}",
            "title": f"Doc for {s['standard_number']}",
            "clause": s["clauses"][0]["clause"],
            "page": None,
            "url": f"https://example.com/{i}",
            "excerpt": s["clauses"][0]["excerpt"],
            "standard_number": s["standard_number"],
            "section": None,
            "licence_class": "full_text_ok",
        }
        for i, s in enumerate(sections)
    ]


# ------------------------------------------------------------ guard


def test_guard_accepts_evidence_is_numbers():
    sections = make_sections(
        ("IS 8921:2024", "7", "Every rope shall be marked with the IS number."),
    )
    assert _guard_is_numbers(
        "Marking is required per IS 8921:2024 clause 7.", sections
    ) is not None


def test_guard_rejects_invented_is_number():
    sections = make_sections(
        ("IS 8921:2024", "7", "Every rope shall be marked."),
    )
    hallucinated = "Also relevant is IS 99999:2031 for ropes."
    assert _guard_is_numbers(hallucinated, sections) is None


# ------------------------------------------------------- confidence


def test_confidence_insufficient_when_no_citations():
    assert (
        determine_confidence(0, 0, 0.0, False) == "INSUFFICIENT_EVIDENCE"
    )


def test_confidence_verified_requires_standard_and_evidence():
    assert (
        determine_confidence(4, 10, 0.7, True) == "VERIFIED"
    )


def test_confidence_never_percentages():
    states = determine_confidence(2, 10, 0.5, True)
    assert states in ("VERIFIED", "STRONG_EVIDENCE", "NEEDS_CLARIFICATION", "INSUFFICIENT_EVIDENCE")


# ------------------------------------------------------- build_response


def test_insufficient_evidence_fixed_string():
    resp = build_response([], [], "anything", "INSUFFICIENT_EVIDENCE")
    assert resp["answer"] == "Insufficient verified evidence."
    assert resp["confidence_state"] == "INSUFFICIENT_EVIDENCE"


def test_response_schema_keys():
    sections = make_sections(
        ("IS 8921:2024", "1", "Covers steel wire ropes."),
        ("IS 8921:2024", "7", "Marking requirements apply."),
        ("IS 12034:2025", "1", "Portable fire extinguishers."),
    )
    resp = build_response(sections, make_citations(sections), "q", "VERIFIED")
    assert set(resp.keys()) == {
        "answer", "sections", "standards", "attributes", "route",
        "laboratories", "certification", "testing",
        "next_steps", "citations", "confidence_state", "clarification_question",
    }
    assert "Insufficient" not in resp["answer"]
    assert resp["standards"][0]["is_number"] == "IS 8921:2024"


def test_extractive_answer_never_mentions_unguarded_numbers():
    sections = make_sections(
        ("IS 8921:2024", "7", "Ropes shall be marked."),
    )
    hallucinated = "Per IS 424242:1999 ropes must be marked."
    resp = build_response(
        sections,
        make_citations(sections),
        "q",
        "VERIFIED",
        grounded_answer=hallucinated,
    )
    assert "IS 424242" not in resp["answer"]


# ------------------------------------------------------- citations


def test_citation_shape():
    sections = make_sections(("IS 8921:2024", "7", "Marking text."))
    cits = make_citations(sections)
    required = {"source_id", "document_id", "title", "clause", "page", "url", "excerpt"}
    assert required <= set(cits[0].keys())


def test_metadata_only_citations_have_no_excerpt():
    """licence_class metadata_only → excerpt must be None/absent."""
    rec = {
        "chunk": type("C", (), {"id": "chunk-1", "text": "secret", "clause_node_id": None})(),
        "document": {"id": "doc-1", "title": "t", "is_number": "IS 9999"},
        "source": {"id": "s1", "url": "u", "licence_class": "metadata_only"},
        "meta": {"clause": "1"},
        "evidence_text": None,
        "score": 1.0,
    }
    out = assemble_citations([rec])
    assert out[0]["excerpt"] is None


# ------------------------------------------------------- embeddings


def test_embedding_deterministic_and_normalised():
    a = embed_text("steel wire ropes for hoisting")
    b = embed_text("steel wire ropes for hoisting")
    assert a == b
    norm = sum(v * v for v in a) ** 0.5
    assert abs(norm - 1.0) < 1e-6
    assert len(a) == 1536


def test_embedding_similarity_lexical():
    from app.rag.embeddings import cosine_similarity
    a = embed_text("steel wire rope breaking load")
    b = embed_text("steel wire rope minimum breaking load")
    c = embed_text("portable fire extinguisher water type")
    assert cosine_similarity(a, b) > cosine_similarity(a, c)


# ------------------------------------------------------- pipeline (SQLite)


def _doc_text(clauses: dict[str, str]) -> str:
    return "\n\n".join(clauses.values())


CLAUSES = {
    "1": "This standard covers steel wire ropes for hoisting, hauling and towing.",
    "7": "Every rope shall be marked with the IS number and the class of rope.",
}


@pytest.mark.asyncio
async def _seed_fixture():
    """Insert one full_text_ok document with clauses into the temp DB."""
    from datetime import datetime, timezone

    from sqlalchemy import select

    async with async_session_local() as db:
        # Idempotent: the conftest DB is shared across tests in a run.
        existing = (
            await db.execute(
                select(Source).where(Source.url == "https://example.com/test-std.pdf")
            )
        ).scalar_one_or_none()
        if existing:
            return

        src = Source(
            url="https://example.com/test-std.pdf",
            title="Test Standard IS 9999:2024",
            publisher="Test Publisher",
            source_type="indigenous_pdf",
            licence_class="full_text_ok",
            retrieved_at=datetime.now(timezone.utc),
            content_hash="0" * 64,
            meta={},
        )
        db.add(src)
        await db.flush()
        doc = Document(
            source_id=src.id,
            version=1,
            content_hash="0" * 64,
            is_number="IS 9999:2024",
            title="Test Standard IS 9999:2024",
            raw_text=_doc_text(CLAUSES),
            parsed_at=datetime.now(timezone.utc),
            licence_class="full_text_ok",
        )
        db.add(doc)
        await db.flush()
        for i, (path, text) in enumerate(CLAUSES.items()):
            db.add(Chunk(
                document_id=doc.id,
                chunk_index=i,
                text=text,
                token_count=len(text.split()),
                meta={"clause_path": path, "section": "", "depth": 1},
                search_vector=text,
                embedding=embed_text(text),
            ))
        await db.commit()


@pytest.mark.asyncio
async def test_pipeline_insufficient_on_gibberish():
    await _seed_fixture()
    async with async_session_local() as db:
        r = await run_pipeline(db, " xylophone quantum ballet wubble ")
        assert r["confidence_state"] in ("INSUFFICIENT_EVIDENCE", "NEEDS_CLARIFICATION")
        assert (
            r["answer"] == "Insufficient verified evidence."
            or r["clarification_question"]
        )


@pytest.mark.asyncio
async def test_pipeline_is_lookup():
    await _seed_fixture()
    async with async_session_local() as db:
        r = await run_pipeline(
            db, "marking requirements IS 9999:2024",
            standard_number="IS 9999:2024",
        )
        assert r["confidence_state"] in ("VERIFIED", "STRONG_EVIDENCE")
        assert any(s["is_number"] == "IS 9999:2024" for s in r["standards"])
        assert "Insufficient" not in r["answer"]


@pytest.mark.asyncio
async def test_pipeline_metadata_only_never_exposes_text():
    """Even when retrieval matches, metadata_only sources expose no text."""
    await _seed_fixture()
    # Flip the source to metadata_only and wipe raw text, as the pipeline
    # would see for priced publications.
    from sqlalchemy import update
    async with async_session_local() as db:
        await db.execute(
            update(Source).values(licence_class="metadata_only")
        )
        await db.execute(update(Document).values(raw_text=None))
        await db.commit()

    async with async_session_local() as db:
        r = await run_pipeline(
            db, "marked rope class",
            standard_number="IS 9999:2024",
        )
        for c in r["citations"]:
            if c.get("licence_class") == "metadata_only":
                assert not c.get("excerpt")
            assert "shall be marked" not in (c.get("excerpt") or "")
