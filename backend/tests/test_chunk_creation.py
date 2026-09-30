"""Chunk creation: token counts, clause linkage, pgvector column."""

import uuid

from sqlalchemy import select
from sqlalchemy.schema import CreateTable
from sqlalchemy.dialects import postgresql

from app.ingestion.pipeline import Pipeline
from app.models import Chunk, ClauseNode, Document, Source

from conftest import SAMPLE_QCO_HTML, make_source_record


async def test_chunk_rows_are_well_formed(db):
    result = await Pipeline(db).process(make_source_record(SAMPLE_QCO_HTML))
    assert result["status"] == "completed"

    doc = (
        await db.execute(
            select(Document).where(
                Document.id == uuid.UUID(result["document_id"])
            )
        )
    ).scalar_one()
    chunks = (
        (
            await db.execute(
                select(Chunk)
                .where(Chunk.document_id == doc.id)
                .order_by(Chunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    assert chunks
    # chunk_index is dense and ordered from 0
    assert [c.chunk_index for c in chunks] == list(range(len(chunks)))
    for chunk in chunks:
        assert chunk.text
        # token_count is derived from the text it is stored with — no
        # self-referential construction bugs.
        assert chunk.token_count == len(chunk.text.split())
        assert chunk.meta["clause_path"] is not None
        assert chunk.embedding is None  # not embedded yet (RAG out of scope)


async def test_chunks_reference_persisted_clause_nodes(db):
    result = await Pipeline(db).process(make_source_record(SAMPLE_QCO_HTML))
    doc_id = uuid.UUID(result["document_id"])

    chunks = (
        (
            await db.execute(
                select(Chunk).where(Chunk.document_id == doc_id).order_by(Chunk.chunk_index)
            )
        )
        .scalars()
        .all()
    )
    nodes = (
        (await db.execute(select(ClauseNode).where(ClauseNode.document_id == doc_id)))
        .scalars()
        .all()
    )
    node_ids = {str(n.id) for n in nodes}
    node_paths = {str(n.clause_path): str(n.id) for n in nodes}

    assert chunks and nodes
    for chunk in chunks:
        # The original bug: clause nodes were never persisted, so this id was
        # always None / dangling. Now every clause chunk must resolve.
        assert chunk.clause_node_id is not None
        assert str(chunk.clause_node_id) in node_ids
        expected = node_paths.get(chunk.meta["clause_path"])
        if expected is not None:
            assert str(chunk.clause_node_id) == expected


async def test_clause_hierarchy_persists_with_parents(db):
    result = await Pipeline(db).process(make_source_record(SAMPLE_QCO_HTML))
    nodes = (
        (
            await db.execute(
                select(ClauseNode).where(
                    ClauseNode.document_id == uuid.UUID(result["document_id"])
                )
            )
        )
        .scalars()
        .all()
    )
    by_path = {n.clause_path: n for n in nodes}
    # SAMPLE_QCO_HTML contains 3.1 and its child 3.1.1
    assert "3.1" in by_path and "3.1.1" in by_path

    child = by_path["3.1.1"]
    assert str(child.parent_id) == str(by_path["3.1"].id)
    assert child.depth == by_path["3.1"].depth + 1
    assert child.depth == 3  # len("3.1.1".split("."))


def test_chunk_ddl_declares_pgvector_vector():
    ddl = str(CreateTable(Chunk.__table__).compile(dialect=postgresql.dialect()))
    assert "VECTOR(1536)" in ddl, "embedding must be a real pgvector column"


def test_json_column_defaults_are_callables_not_shared_dicts():
    """Regression: Column(JSON, default={}) shares one dict across rows."""
    for table in (Source.__table__, Chunk.__table__):
        default = table.c["meta"].default
        assert default is not None and callable(default.arg)
