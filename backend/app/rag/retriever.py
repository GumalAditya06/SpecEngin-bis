"""Hybrid retrieval: full-text/keyword + vector search + metadata filtering.

Portable across dialects:
- PostgreSQL: to_tsquery full-text over chunks.search_vector + pgvector
  cosine_distance over chunks.embedding.
- SQLite (local dev/tests): keyword matching over search_vector (which
  stores raw text there) + cosine similarity over embedding JSON arrays.

Evidence texts are attached only when the source licence_class is
full_text_ok — the licence invariant is never bypassed by retrieval.
"""

import json
import logging
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Chunk, Document, Source, Standard
from app.rag.embeddings import STOPWORDS, cosine_similarity, embed_query

logger = logging.getLogger("specengine-bis.rag.retriever")


async def retrieve_hybrid(
    db: AsyncSession,
    query: str,
    *,
    standard_number: Optional[str] = None,
    source_type: Optional[str] = None,
    licence_class: Optional[str] = None,
    min_relevance: float = 0.05,
    limit: int = 20,
) -> list[dict]:
    """Run hybrid retrieval and return enriched chunk records with scores."""
    results: dict[str, dict] = {}

    if query.strip():
        await _keyword_branch(db, query, results, limit)
        await _vector_branch(db, query, results, limit)

    await _metadata_branch(
        db, results, limit,
        standard_number=standard_number,
        source_type=source_type,
        licence_class=licence_class,
    )

    # Merge, filter, sort.
    filtered = [v for v in results.values() if v["score"] >= min_relevance]
    filtered.sort(key=lambda v: v["score"], reverse=True)

    # Enrich with document/source/standard metadata.
    enriched = []
    for rec in filtered[:limit]:
        chunk = rec["chunk"]
        doc = await _load(db, chunk.document_id, Document)
        src = await _load(db, doc.source_id, Source) if doc else None
        std = (
            await _load(db, doc.standard_id, Standard)
            if doc and doc.standard_id
            else None
        )

        licence = src.licence_class if src else (doc.licence_class if doc else None)
        full_ok = licence == "full_text_ok"
        chunk_meta = chunk.meta or {}

        enriched.append({
            "chunk": chunk,
            "score": round(min(rec["score"], 1.5), 4),
            "search_source": rec["source"],
            "document": {
                "id": str(doc.id) if doc else None,
                "is_number": doc.is_number if doc else None,
                "title": doc.title if doc else None,
                "version": doc.version if doc else None,
                "parsed_at": doc.parsed_at.isoformat() if doc and doc.parsed_at else None,
                "licence_class": doc.licence_class if doc else None,
                "standard_id": str(doc.standard_id) if doc and doc.standard_id else None,
            },
            "source": {
                "id": str(src.id) if src else None,
                "url": src.url if src else None,
                "publisher": src.publisher if src else None,
                "source_type": src.source_type if src else None,
                "retrieved_at": src.retrieved_at.isoformat() if src and src.retrieved_at else None,
                "licence_class": src.licence_class if src else None,
            },
            "standard": {
                "id": str(std.id),
                "is_number": std.is_number,
                "title": std.title,
                "sector": std.sector,
                "year": std.year,
                "status": std.status,
            } if std else None,
            "meta": {
                # Chunk metadata contract: document, source, standard number,
                # section, clause, page (when available), source URL,
                # publication/version info.
                **chunk_meta,
                "document_id": str(chunk.document_id),
                "source_id": str(src.id) if src else None,
                "standard_number": std.is_number if std else (doc.is_number if doc else None),
                "section": chunk_meta.get("section"),
                "clause": chunk_meta.get("clause_path"),
                "page": chunk_meta.get("page"),
                "source_url": src.url if src else chunk_meta.get("source_url"),
                "publisher": src.publisher if src else None,
                "version": doc.version if doc else None,
                "retrieved_at": src.retrieved_at.isoformat() if src and src.retrieved_at else None,
            },
            # Licence gate: evidence text only for full_text_ok sources.
            "evidence_text": chunk.text if full_ok else None,
        })

    return enriched


# ------------------------------------------------------------- branches


async def _keyword_branch(db, query, results, limit):
    """Full-text on Postgres; keyword overlap on SQLite."""
    q = query.strip()
    if not q:
        return
    if db.bind.dialect.name == "postgresql":
        await _pg_fts(db, q, results, limit)
        return

    # Drop stopwords: "LIKE '%the%'" must not match every chunk.
    terms = [
        t for t in q.lower().split()
        if len(t) > 2 and t not in STOPWORDS
    ]
    if not terms:
        return
    try:
        conds = [Chunk.search_vector.ilike(f"%{t}%") for t in terms]
        stmt = (
            select(Chunk)
            .where(Chunk.search_vector.isnot(None))
            .where(or_(*conds))
            .limit(limit * 2)
        )
        rows = (await db.execute(stmt)).scalars().all()
        for chunk in rows:
            text = (chunk.search_vector or chunk.text or "").lower()
            hits = sum(1 for t in terms if t in text)
            score = hits / len(terms)
            if score > 0:
                _merge(results, chunk, score, "keyword")
    except Exception as exc:
        logger.warning("keyword branch failed: %s", exc)


async def _pg_fts(db, q, results, limit):
    """PostgreSQL full-text search with an ILIKE fallback."""
    terms = [w for w in q.lower().split() if w.isalnum()][:8]
    try:
        tsquery = func.to_tsquery("english", " & ".join(terms))
        rank = func.ts_rank(Chunk.search_vector, tsquery)
        stmt = (
            select(Chunk, rank.label("rank"))
            .where(Chunk.search_vector.isnot(None))
            .where(Chunk.search_vector.op("@@")(tsquery))
            .order_by(rank.desc())
            .limit(limit * 2)
        )
        rows = (await db.execute(stmt)).all()
        for row in rows:
            _merge(results, row.Chunk, min(float(row.rank) * 4, 1.0), "fts")
        return
    except Exception as exc:
        logger.warning("PG full-text failed (%s); falling back to ILIKE", exc)

    try:
        conds = [Chunk.search_vector.ilike(f"%{w}%") for w in terms if len(w) > 2]
        if not conds:
            return
        rows = (
            (await db.execute(
                select(Chunk)
                .where(Chunk.search_vector.isnot(None))
                .where(or_(*conds))
                .limit(limit * 2)
            ))
            .scalars()
            .all()
        )
        for chunk in rows:
            _merge(results, chunk, 0.4, "keyword")
    except Exception as exc2:
        logger.warning("PG ILIKE fallback failed: %s", exc2)


async def _vector_branch(db, query, results, limit):
    """pgvector cosine_distance on Postgres; Python cosine on SQLite."""
    qvec = embed_query(query)
    if not any(qvec):
        return

    if db.bind.dialect.name == "postgresql":
        try:
            distance = Chunk.embedding.cosine_distance(qvec)
            stmt = (
                select(Chunk, distance.label("distance"))
                .where(Chunk.embedding.isnot(None))
                .order_by(distance.asc())
                .limit(limit * 2)
            )
            rows = (await db.execute(stmt)).all()
            for row in rows:
                relevance = max(0.0, 1.0 - float(row.distance))
                if relevance > 0.05:
                    _merge(results, row.Chunk, relevance, "vector")
            return
        except Exception as exc:
            logger.warning("pgvector branch failed: %s", exc)

    # SQLite path: load embedded chunks and score in Python.
    try:
        rows = (
            (await db.execute(
                select(Chunk).where(Chunk.embedding.isnot(None)).limit(500)
            ))
            .scalars()
            .all()
        )
        for chunk in rows:
            try:
                cvec = json.loads(chunk.embedding) if isinstance(chunk.embedding, str) else list(chunk.embedding)
            except (TypeError, ValueError):
                continue
            sim = cosine_similarity(qvec, cvec)
            if sim > 0.05:
                _merge(results, chunk, sim, "vector")
    except Exception as exc:
        logger.warning("vector branch (sqlite) failed: %s", exc)


async def _metadata_branch(
    db, results, limit, *, standard_number, source_type, licence_class
):
    """Metadata filtering by standard number / source type / licence."""
    meta_conditions = []
    if standard_number:
        meta_conditions.append(Document.is_number.ilike(f"%{standard_number}%"))
        meta_conditions.append(
            Document.standard_id.in_(
                select(Standard.id).where(
                    Standard.is_number.ilike(f"%{standard_number}%")
                )
            )
        )
    if source_type:
        meta_conditions.append(Source.source_type == source_type)
    if licence_class:
        meta_conditions.append(Source.licence_class == licence_class)
    if not meta_conditions:
        return
    try:
        stmt = (
            select(Chunk)
            .join(Document, Chunk.document_id == Document.id)
            .join(Source, Document.source_id == Source.id)
            .where(or_(*meta_conditions))
            .limit(limit * 2)
        )
        rows = (await db.execute(stmt)).scalars().all()
        for chunk in rows:
            _merge(results, chunk, 0.5, "metadata")
    except Exception as exc:
        logger.warning("metadata branch failed: %s", exc)


def _merge(results: dict, chunk, score: float, source_tag: str) -> None:
    cid = str(chunk.id)
    if cid not in results:
        results[cid] = {"chunk": chunk, "score": score, "source": source_tag}
    else:
        results[cid]["score"] = max(results[cid]["score"], score)
        if source_tag not in results[cid]["source"]:
            results[cid]["source"] += f"+{source_tag}"


async def _load(db: AsyncSession, uid, model):
    if not uid:
        return None
    row = await db.execute(select(model).where(model.id == uid))
    return row.scalar_one_or_none()
