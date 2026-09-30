"""CLAUDE.md hard invariant: licence compliance gate + DB CHECK constraints.

CI gate per CLAUDE.md: ``pytest tests/test_licence_invariant.py``.
"""

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.ingestion.pipeline import LicenceViolationError, Pipeline
from app.models import Chunk, ClauseNode, Document, Source

from conftest import (
    SAMPLE_QCO_HTML,
    SAMPLE_PDF_LINES,
    make_pdf_bytes,
    make_source_record,
)

SCHEME_HTML = (
    "<html><head><title>Scheme-I Certification Regulations</title></head>"
    "<body><h1>Scheme of Inspection and Testing, Scheme-I</h1>"
    "<p>3.1 Grant of licence</p>"
    "<p>Secret licensed manufacturing detail that must never be stored.</p>"
    "<p>3.1.1 Validity period of the licence</p>"
    "<p>Another restricted body paragraph.</p>"
    "</body></html>"
).encode("utf-8")


async def test_parser_claiming_full_text_for_metadata_only_is_rejected(db):
    """A qco parser (full_text_ok) under a metadata_only source must fail loud."""
    record = make_source_record(
        SAMPLE_QCO_HTML,
        source_type="qco",
        licence_class="metadata_only",
    )
    with pytest.raises(LicenceViolationError):
        await Pipeline(db).process(record)

    # Nothing was persisted on the failed run.
    assert (await db.execute(select(func.count(Source.id)))).scalar_one() == 0
    assert (await db.execute(select(func.count(Document.id)))).scalar_one() == 0


async def test_unknown_licence_class_is_rejected(db):
    record = make_source_record(SAMPLE_QCO_HTML, licence_class="who-knows")
    with pytest.raises(LicenceViolationError, match="unknown licence_class"):
        await Pipeline(db).process(record)


async def test_metadata_only_ingestion_stores_no_body_text(db):
    record = make_source_record(
        SCHEME_HTML,
        url="https://www.bis.gov.in/certification_schemes/scheme-i",
        source_type="scheme_reg",
        licence_class="metadata_only",
        title="Scheme-I regulations",
    )
    result = await Pipeline(db).process(record)
    assert result["status"] == "completed"

    doc = (
        await db.execute(
            select(Document).where(
                Document.id == uuid.UUID(result["document_id"])
            )
        )
    ).scalar_one()
    assert doc.raw_text is None, "metadata_only must never store full text"

    chunks = (
        (
            await db.execute(select(Chunk).where(Chunk.document_id == doc.id))
        )
        .scalars()
        .all()
    )
    assert chunks
    for chunk in chunks:
        # Clause headings (structure) are fine; body paragraphs are not.
        assert "restricted body paragraph" not in chunk.text
        assert "Secret licensed" not in chunk.text

    # Clause structure itself IS stored — that is metadata, not full text.
    nodes = (
        (
            await db.execute(
                select(ClauseNode).where(ClauseNode.document_id == doc.id)
            )
        )
        .scalars()
        .all()
    )
    assert {n.clause_path for n in nodes} >= {"3.1", "3.1.1"}


async def test_metadata_only_pdf_source_stores_no_full_text(db):
    pdf = make_pdf_bytes(SAMPLE_PDF_LINES)
    record = make_source_record(
        pdf,
        url="https://www.bis.gov.in/schemes/iso-adopted.pdf",
        source_type="scheme_reg",
        licence_class="metadata_only",
    )
    result = await Pipeline(db).process(record)
    doc = (
        await db.execute(
            select(Document).where(
                Document.id == uuid.UUID(result["document_id"])
            )
        )
    ).scalar_one()
    assert doc.raw_text is None


async def test_sources_check_constraint_rejects_bad_licence_class(db):
    db.add(
        Source(
            url="https://example.org/x",
            title="t",
            publisher="p",
            source_type="qco",
            licence_class="not_a_licence_class",
            retrieved_at=datetime.now(timezone.utc),
            content_hash="deadbeef",
            meta={},
        )
    )
    with pytest.raises(IntegrityError):
        await db.commit()
