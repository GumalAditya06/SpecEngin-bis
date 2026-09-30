"""End-to-end ingestion of a sample document, including versioning."""

from sqlalchemy import func, select

from app.ingestion.pipeline import Pipeline
from app.models import Chunk, ClauseNode, Document, Source

from conftest import SAMPLE_QCO_HTML, make_source_record


async def _counts(db):
    out = {}
    for model in (Source, Document, ClauseNode, Chunk):
        out[model.__tablename__] = (
            await db.execute(select(func.count(model.id)))
        ).scalar_one()
    return out


async def test_ingest_sample_document(db):
    record = make_source_record(SAMPLE_QCO_HTML)
    result = await Pipeline(db).process(record)

    assert result["status"] == "completed"
    assert result["version"] == 1
    assert result["clauses_extracted"] > 0
    assert result["chunks_created"] > 0

    source = (
        await db.execute(select(Source).where(Source.url == record.url))
    ).scalar_one()
    assert source.content_hash == record.content_hash
    assert source.licence_class == "full_text_ok"

    doc = (
        await db.execute(
            select(Document).where(Document.source_id == source.id)
        )
    ).scalar_one()
    assert doc.version == 1
    assert doc.content_hash == record.content_hash
    assert doc.raw_text  # full_text_ok source keeps its text
    assert doc.title

    counts = await _counts(db)
    assert counts["clause_nodes"] == result["clauses_extracted"]
    assert counts["chunks"] == result["chunks_created"]


async def test_reingest_unchanged_content_is_skipped(db):
    record = make_source_record(SAMPLE_QCO_HTML)
    first = await Pipeline(db).process(record)
    counts_after_first = await _counts(db)

    second = await Pipeline(db).process(make_source_record(SAMPLE_QCO_HTML))
    assert second == {"status": "skipped", "reason": "unchanged"}

    assert await _counts(db) == counts_after_first
    assert first["version"] == 1


async def test_reingest_changed_content_creates_next_version(db):
    v1 = await Pipeline(db).process(make_source_record(SAMPLE_QCO_HTML))

    changed = SAMPLE_QCO_HTML.replace(
        b"Steel wire ropes", b"Alloy steel wire ropes (revised)"
    )
    assert changed != SAMPLE_QCO_HTML
    v2 = await Pipeline(db).process(
        make_source_record(changed, url=make_source_record(SAMPLE_QCO_HTML).url)
    )

    assert v1["version"] == 1
    assert v2["status"] == "completed"
    assert v2["version"] == 2

    # One immutable source row, two document versions.
    sources = (await db.execute(select(Source))).scalars().all()
    assert len(sources) == 1
    source = sources[0]

    docs = (
        (
            await db.execute(
                select(Document)
                .where(Document.source_id == source.id)
                .order_by(Document.version)
            )
        )
        .scalars()
        .all()
    )
    assert [d.version for d in docs] == [1, 2]
    assert docs[0].content_hash != docs[1].content_hash
    assert docs[1].content_hash == (
        __import__("hashlib").sha256(changed).hexdigest()
    )

    # Both versions carry their own clauses and chunks.
    for doc in docs:
        nodes = (
            await db.execute(select(ClauseNode).where(ClauseNode.document_id == doc.id))
        ).scalars().all()
        chunks = (
            await db.execute(select(Chunk).where(Chunk.document_id == doc.id))
        ).scalars().all()
        assert nodes
        assert chunks


async def test_different_urls_with_identical_content_do_not_collide(db):
    """content_hash is per-row metadata, not a global unique key."""
    a = make_source_record(
        SAMPLE_QCO_HTML, url="https://www.bis.gov.in/qco/original"
    )
    b = make_source_record(
        SAMPLE_QCO_HTML, url="https://mirror.example.org/qco/original"
    )
    r1 = await Pipeline(db).process(a)
    r2 = await Pipeline(db).process(b)
    assert r1["status"] == r2["status"] == "completed"
    sources = (await db.execute(select(Source))).scalars().all()
    assert len(sources) == 2
    assert len({s.content_hash for s in sources}) == 1
