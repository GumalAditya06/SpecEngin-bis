import asyncio
import json
import logging
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import Select, String, and_, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rag.pipeline import run_pipeline
from app.core.db import get_db
from app.models.base import (
    Amendment,
    CertificationScheme,
    ClauseNode,
    Chunk,
    ComplianceCase,
    ComplianceCaseStandard,
    Document,
    Laboratory,
    Source,
    Standard,
    Test,
    standard_laboratory,
    standard_related,
)

logger = logging.getLogger("specengine-bis.api")

router = APIRouter()

SERVICE_NAME = "specengine-bis"

SOURCE_TYPES = {
    "indigenous_pdf": "Indian Standard",
    "qco": "Quality Control Order",
    "scheme_reg": "Certification scheme",
    "hallmarking_docs": "Hallmarking document",
    "faq_pages": "FAQ page",
    "lab_lists": "Lab list",
    "kys_catalogue": "Catalogue",
    "ISO_adopted": "ISO-adopted standard",
}


async def _clause_tree_for(db: AsyncSession, document_id) -> list[dict]:
    """Build the nested clause tree for a document."""
    clauses_rows = (
        (await db.execute(
            select(ClauseNode)
            .where(ClauseNode.document_id == document_id)
            .order_by(ClauseNode.depth.asc(), ClauseNode.order_index.asc())
        ))
        .scalars()
        .all()
    )
    children: dict = {}
    for c in clauses_rows:
        node = {
            "id": str(c.id),
            "clause_path": c.clause_path,
            "title": c.title,
            "depth": c.depth,
            "order_index": c.order_index,
            "children": [],
        }
        children.setdefault(str(c.parent_id) if c.parent_id else None, []).append(node)
    tree = children.get(None, [])
    stack = list(tree)
    while stack:
        node = stack.pop()
        kids = children.get(node["id"], [])
        if kids:
            node["children"] = kids
            stack.extend(kids)
    return tree


def _doc_payload(
    d: Document,
    s: Source,
    snippet: Optional[str],
    clause_count: int,
    chunk_count: int,
) -> dict:
    meta = s.meta or {}
    return {
        "id": str(d.id),
        "is_number": d.is_number,
        "title": d.title or "",
        "version": d.version,
        "source_type": s.source_type,
        "source_type_label": SOURCE_TYPES.get(s.source_type, s.source_type),
        "licence_class": s.licence_class,
        "publisher": s.publisher,
        "url": s.url,
        "retrieved_at": s.retrieved_at.isoformat() if s.retrieved_at else None,
        "department": meta.get("department"),
        "product": meta.get("product"),
        "notified_on": meta.get("notified_on"),
        "effective_on": meta.get("effective_on"),
        "sector": meta.get("sector"),
        "snippet": snippet if s.licence_class == "full_text_ok" else None,
        "clause_count": clause_count,
        "chunk_count": chunk_count,
    }


@router.get("/health")
async def health() -> dict:
    """Liveness probe — no dependencies, always honest about the service."""
    return {"status": "ok", "service": SERVICE_NAME}


@router.get("/health/db")
async def health_db(db: AsyncSession = Depends(get_db)):
    """Database connectivity probe — returns 503 when the DB is unreachable."""
    try:
        await db.execute(select(1))
    except Exception as exc:  # pragma: no cover - depends on live DB state
        logger.warning("database health check failed: %s", type(exc).__name__)
        return JSONResponse(
            status_code=503,
            content={
                "status": "error",
                "service": SERVICE_NAME,
                "database": "unavailable",
            },
        )
    return {
        "status": "ok",
        "service": SERVICE_NAME,
        "database": "ok",
    }


@router.get("/stats")
async def stats(db: AsyncSession = Depends(get_db)):
    total_sources = (await db.execute(select(func.count(Source.id)))).scalar_one()
    total_docs = (await db.execute(select(func.count(Document.id)))).scalar_one()
    total_clauses = (
        await db.execute(select(func.count(ClauseNode.id)))
    ).scalar_one()
    total_chunks = (await db.execute(select(func.count(Chunk.id)))).scalar_one()

    type_rows = (
        await db.execute(
            select(Source.source_type, func.count(Source.id))
            .group_by(Source.source_type)
            .order_by(func.count(Source.id).desc())
        )
    ).all()
    licence_rows = (
        await db.execute(
            select(Source.licence_class, func.count(Source.id))
            .group_by(Source.licence_class)
        )
    ).all()

    return {
        "sources": total_sources,
        "documents": total_docs,
        "clause_nodes": total_clauses,
        "chunks": total_chunks,
        "standards": (
            await db.execute(select(func.count(Standard.id)))
        ).scalar_one(),
        "laboratories": (
            await db.execute(select(func.count(Laboratory.id)))
        ).scalar_one(),
        "by_source_type": [
            {
                "type": t,
                "label": SOURCE_TYPES.get(t, t),
                "count": c,
            }
            for t, c in type_rows
        ],
        "by_licence": [{"licence_class": l, "count": c} for l, c in licence_rows],
    }


def _base_search_query(
    q: Optional[str],
    source_type: Optional[str],
    licence: Optional[str],
) -> Select:
    clause_count = (
        select(func.count())
        .where(ClauseNode.document_id == Document.id)
        .scalar_subquery()
        .label("clause_count")
    )
    chunk_count = (
        select(func.count())
        .where(Chunk.document_id == Document.id)
        .scalar_subquery()
        .label("chunk_count")
    )
    snippet = func.substr(Document.raw_text, 1, 420).label("snippet")

    stmt = (
        select(
            Document, Source, snippet, clause_count, chunk_count
        )
        .join(Source, Document.source_id == Source.id)
        .where(Document.source_id.isnot(None))
    )

    if q:
        like = f"%{q}%"
        conditions = [
            Document.title.ilike(like),
            Document.is_number.ilike(like),
            Document.raw_text.ilike(like),
            cast(Source.meta, String).ilike(like),
        ]
        stmt = stmt.where(or_(*conditions))
    if source_type:
        stmt = stmt.where(Source.source_type == source_type)
    if licence:
        stmt = stmt.where(Source.licence_class == licence)
    return stmt


@router.get("/search")
async def search(
    q: Optional[str] = Query(None),
    source_type: Optional[str] = Query(None),
    licence: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    if q and q.strip() == "":
        q = None
    q = q.strip() if q else None

    base = _base_search_query(q, source_type, licence)
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Document.title.asc()).limit(limit).offset(offset)
        )
    ).all()

    results = [
        _doc_payload(
            d, s, snippet, int(c1) if c1 else 0, int(c2) if c2 else 0
        )
        for d, s, snippet, c1, c2 in rows
    ]
    return {"total": total, "limit": limit, "offset": offset, "results": results}


@router.get("/documents/{document_id}")
async def document_detail(
    document_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    row = (
        await db.execute(
            select(Document, Source)
            .join(Source, Document.source_id == Source.id)
            .where(Document.id == document_id)
        )
    ).first()
    if not row:
        raise HTTPException(status_code=404, detail="Document not found")
    doc, src = row

    tree = await _clause_tree_for(db, doc.id)

    chunks_rows = (
        await db.execute(
            select(Chunk)
            .where(Chunk.document_id == doc.id)
            .order_by(Chunk.chunk_index.asc())
        )
    ).scalars().all()

    chunks = [
        {
            "chunk_index": ch.chunk_index,
            "clause_node_id": str(ch.clause_node_id) if ch.clause_node_id else None,
            "clause_path": (ch.meta or {}).get("clause_path"),
            "text": ch.text if src.licence_class == "full_text_ok" else None,
        }
        for ch in chunks_rows
    ]

    return {
        "id": str(doc.id),
        "is_number": doc.is_number,
        "title": doc.title,
        "version": doc.version,
        "licence_class": src.licence_class,
        "source": {
            "id": str(src.id),
            "url": src.url,
            "publisher": src.publisher,
            "source_type": src.source_type,
            "source_type_label": SOURCE_TYPES.get(src.source_type, src.source_type),
            "retrieved_at": src.retrieved_at.isoformat() if src.retrieved_at else None,
            "meta": src.meta,
        },
        "full_text": (doc.raw_text or "")[:4000] if src.licence_class == "full_text_ok" else None,
        "clause_tree": tree,
        "chunks": chunks,
    }


@router.get("/types")
async def types():
    return {
        "source_types": [
            {"type": t, "label": l} for t, l in SOURCE_TYPES.items()
        ]
    }


# ---------------------------------------------------------------- Standards


def _std_summary(s: Standard, document_count: int) -> dict:
    return {
        "id": str(s.id),
        "is_number": s.is_number,
        "title": s.title,
        "status": s.status,
        "sector": s.sector,
        "year": s.year,
        "description": s.description,
        "document_count": document_count,
        "created_at": s.created_at.isoformat() if s.created_at else None,
    }


@router.get("/standards")
async def list_standards(
    q: Optional[str] = Query(None),
    is_number: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    sector: Optional[str] = Query(None),
    year: Optional[int] = Query(None),
    sort: str = Query("title"),
    order: str = Query("asc"),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    q = q.strip() if q else None
    doc_count = (
        select(func.count())
        .where(Document.standard_id == Standard.id)
        .scalar_subquery()
        .label("document_count")
    )
    stmt = select(Standard, doc_count)
    conditions = []
    if q:
        like = f"%{q}%"
        conditions.append(
            or_(
                Standard.title.ilike(like),
                Standard.is_number.ilike(like),
                Standard.description.ilike(like),
                Standard.sector.ilike(like),
            )
        )
    if is_number:
        conditions.append(Standard.is_number.ilike(f"%{is_number}%"))
    if status:
        conditions.append(Standard.status == status)
    if sector:
        conditions.append(Standard.sector.ilike(f"%{sector}%"))
    if year is not None:
        conditions.append(Standard.year == year)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()

    sort_cols = {
        "title": Standard.title,
        "is_number": Standard.is_number,
        "year": Standard.year,
        "status": Standard.status,
        "sector": Standard.sector,
    }
    col = sort_cols.get(sort, Standard.title)
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())
    rows = (await db.execute(stmt.limit(limit).offset(offset))).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [_std_summary(s, int(c)) for s, c in rows],
    }


@router.get("/standards/{standard_id}")
async def standard_detail(
    standard_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    s = (
        await db.execute(select(Standard).where(Standard.id == standard_id))
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Standard not found")
    doc_count = (
        await db.execute(
            select(func.count()).where(Document.standard_id == s.id)
        )
    ).scalar_one()
    payload = _std_summary(s, int(doc_count))

    # Documents linked to this standard (with source info + snippet)
    doc_rows = (
        await db.execute(
            select(Document, Source)
            .join(Source, Document.source_id == Source.id)
            .where(Document.standard_id == s.id)
            .order_by(Document.version.asc())
        )
    ).all()
    documents = []
    for d, src in doc_rows:
        payload_doc = _doc_payload(d, src, None, 0, 0)
        payload_doc["snippet"] = (
            (d.raw_text or "")[:420] if src.licence_class == "full_text_ok" else None
        )
        payload_doc["standard_id"] = str(s.id)
        documents.append(payload_doc)

    amendments = (
        await db.execute(
            select(Amendment)
            .where(Amendment.standard_id == s.id)
            .order_by(Amendment.effective_date.desc())
        )
    ).scalars().all()

    related_rows = (
        await db.execute(
            select(Standard)
            .join(standard_related, standard_related.c.related_standard_id == Standard.id)
            .where(standard_related.c.base_standard_id == s.id)
        )
    ).scalars().all()

    lab_rows = (
        await db.execute(
            select(Laboratory)
            .join(standard_laboratory, standard_laboratory.c.laboratory_id == Laboratory.id)
            .where(standard_laboratory.c.standard_id == s.id)
        )
    ).scalars().all()

    schemes = (
        await db.execute(
            select(CertificationScheme).where(CertificationScheme.standard_id == s.id)
        )
    ).scalars().all()

    payload.update(
        {
            "documents": documents,
            "amendments": [
                {
                    "id": str(a.id),
                    "amendment_number": a.amendment_number,
                    "title": a.title,
                    "effective_date": a.effective_date.isoformat() if a.effective_date else None,
                }
                for a in amendments
            ],
            "related": [
                _std_summary(r, 0) for r in related_rows
            ],
            "laboratories": [
                {
                    "id": str(l.id),
                    "name": l.name,
                    "city": l.city,
                    "state": l.state,
                    "recognition_status": l.recognition_status,
                    "address": l.address,
                    "contact_email": l.contact_email,
                    "standard_count": 0,
                }
                for l in lab_rows
            ],
            "certification_schemes": [
                {"id": str(cs.id), "title": cs.title} for cs in schemes
            ],
        }
    )
    return payload


@router.get("/standards/{standard_id}/documents")
async def standard_documents(
    standard_id: uuid.UUID,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    s = (
        await db.execute(select(Standard).where(Standard.id == standard_id))
    ).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Standard not found")
    base = (
        select(Document, Source)
        .join(Source, Document.source_id == Source.id)
        .where(Document.standard_id == s.id)
    )
    total = (await db.execute(select(func.count()).select_from(base.subquery()))).scalar_one()
    rows = (
        await db.execute(
            base.order_by(Document.version.asc()).limit(limit).offset(offset)
        )
    ).all()
    docs = []
    for d, src in rows:
        p = _doc_payload(d, src, None, 0, 0)
        p["snippet"] = (d.raw_text or "")[:420] if src.licence_class == "full_text_ok" else None
        p["standard_id"] = str(s.id)
        p["clause_tree"] = await _clause_tree_for(db, d.id)
        docs.append(p)
    return {"total": int(total), "documents": docs}


@router.get("/standards/{standard_id}/amendments")
async def standard_amendments(
    standard_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Amendment)
            .where(Amendment.standard_id == standard_id)
            .order_by(Amendment.effective_date.desc())
        )
    ).scalars().all()
    return {
        "standard_id": str(standard_id),
        "amendments": [
            {
                "id": str(a.id),
                "amendment_number": a.amendment_number,
                "title": a.title,
                "effective_date": a.effective_date.isoformat() if a.effective_date else None,
            }
            for a in rows
        ],
    }


@router.get("/standards/{standard_id}/related")
async def standard_related_list(
    standard_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Standard)
            .join(standard_related, standard_related.c.related_standard_id == Standard.id)
            .where(standard_related.c.base_standard_id == standard_id)
        )
    ).scalars().all()
    return {
        "standard_id": str(standard_id),
        "related": [_std_summary(r, 0) for r in rows],
    }


@router.get("/standards/{standard_id}/laboratories")
async def standard_laboratories_list(
    standard_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(Laboratory)
            .join(standard_laboratory, standard_laboratory.c.laboratory_id == Laboratory.id)
            .where(standard_laboratory.c.standard_id == standard_id)
        )
    ).scalars().all()
    return {
        "standard_id": str(standard_id),
        "laboratories": [
            {
                "id": str(l.id),
                "name": l.name,
                "city": l.city,
                "state": l.state,
                "recognition_status": l.recognition_status,
                "address": l.address,
                "contact_email": l.contact_email,
                "standard_count": 0,
            }
            for l in rows
        ],
    }


# ------------------------------------------------------------ Laboratories


def _lab_summary(l: Laboratory, standard_count: int) -> dict:
    return {
        "id": str(l.id),
        "name": l.name,
        "city": l.city,
        "state": l.state,
        "recognition_status": l.recognition_status,
        "address": l.address,
        "contact_email": l.contact_email,
        "standard_count": standard_count,
    }


@router.get("/laboratories")
async def list_laboratories(
    q: Optional[str] = Query(None),
    city: Optional[str] = Query(None),
    state: Optional[str] = Query(None),
    test: Optional[str] = Query(None),
    is_number: Optional[str] = Query(None),
    recognition_status: Optional[str] = Query(None),
    standard_id: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    q = q.strip() if q else None
    std_count = (
        select(func.count())
        .where(standard_laboratory.c.laboratory_id == Laboratory.id)
        .scalar_subquery()
        .label("standard_count")
    )
    stmt = select(Laboratory, std_count)
    conditions = []
    if q:
        like = f"%{q}%"
        conditions.append(
            or_(
                Laboratory.name.ilike(like),
                Laboratory.city.ilike(like),
                Laboratory.state.ilike(like),
                Laboratory.address.ilike(like),
            )
        )
    if city:
        conditions.append(Laboratory.city.ilike(f"%{city}%"))
    if state:
        conditions.append(Laboratory.state.ilike(f"%{state}%"))
    if test:
        # Labs that offer a test whose name matches (e.g. "discharge").
        conditions.append(
            Laboratory.id.in_(
                select(Test.laboratory_id).where(Test.test_name.ilike(f"%{test}%"))
            )
        )
    if is_number:
        # Labs associated with a standard by IS number (e.g. "IS 12034").
        conditions.append(
            Laboratory.id.in_(
                select(standard_laboratory.c.laboratory_id).join(
                    Standard, standard_laboratory.c.standard_id == Standard.id
                ).where(Standard.is_number.ilike(f"%{is_number}%"))
            )
        )
    if recognition_status:
        conditions.append(Laboratory.recognition_status == recognition_status)
    if standard_id:
        conditions.append(
            Laboratory.id.in_(
                select(standard_laboratory.c.laboratory_id).where(
                    standard_laboratory.c.standard_id == standard_id
                )
            )
        )
    if conditions:
        stmt = stmt.where(and_(*conditions))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(Laboratory.name.asc()).limit(limit).offset(offset))
    ).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "results": [_lab_summary(l, int(c)) for l, c in rows],
    }


@router.get("/laboratories/{laboratory_id}")
async def laboratory_detail(
    laboratory_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    l = (
        await db.execute(select(Laboratory).where(Laboratory.id == laboratory_id))
    ).scalar_one_or_none()
    if not l:
        raise HTTPException(status_code=404, detail="Laboratory not found")
    std_rows = (
        await db.execute(
            select(Standard)
            .join(standard_laboratory, standard_laboratory.c.standard_id == Standard.id)
            .where(standard_laboratory.c.laboratory_id == l.id)
        )
    ).scalars().all()
    tests = (
        await db.execute(select(Test).where(Test.laboratory_id == l.id))
    ).scalars().all()
    payload = _lab_summary(l, len(std_rows))
    payload.update(
        {
            "standards": [_std_summary(s, 0) for s in std_rows],
            "tests": [
                {
                    "id": str(t.id),
                    "test_name": t.test_name,
                    "description": t.description,
                    "standard_id": str(t.standard_id) if t.standard_id else None,
                }
                for t in tests
            ],
        }
    )
    return payload


# ----------------------------------------------------------------- Sources


@router.get("/sources")
async def list_sources(
    source_type: Optional[str] = Query(None),
    licence: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    doc_count = (
        select(func.count())
        .where(Document.source_id == Source.id)
        .scalar_subquery()
        .label("document_count")
    )
    stmt = select(Source, doc_count)
    conditions = []
    if source_type:
        conditions.append(Source.source_type == source_type)
    if licence:
        conditions.append(Source.licence_class == licence)
    if conditions:
        stmt = stmt.where(and_(*conditions))
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar_one()
    rows = (
        await db.execute(stmt.order_by(Source.title.asc()).limit(limit).offset(offset))
    ).all()
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "sources": [
            {
                "id": str(s.id),
                "url": s.url,
                "title": s.title,
                "publisher": s.publisher,
                "source_type": s.source_type,
                "source_type_label": SOURCE_TYPES.get(s.source_type, s.source_type),
                "licence_class": s.licence_class,
                "retrieved_at": s.retrieved_at.isoformat() if s.retrieved_at else None,
                "content_hash": s.content_hash,
                "meta": s.meta or {},
                "document_count": int(c),
            }
            for s, c in rows
        ],
    }


# --------------------------------------------------------------- Assistant


class AssistantQuery(BaseModel):
    query: str = Field(..., min_length=1, max_length=2000)
    product_description: Optional[str] = Field(None, max_length=2000)
    standard_number: Optional[str] = Field(None, max_length=100)
    source_type: Optional[str] = Field(None, max_length=50)
    licence_class: Optional[str] = Field(None, max_length=20)


@router.post("/assistant/query")
async def assistant_query(
    body: AssistantQuery,
    db: AsyncSession = Depends(get_db),
):
    """Grounded AI assistant over the BIS index.

    Runs the RAG pipeline (query understanding → hybrid retrieval →
    reranking → evidence assembly → optional LLM generation). Answers are
    grounded in retrieved evidence; IS numbers in any LLM draft must come
    from the retrieval layer. Insufficient evidence yields the fixed string
    "Insufficient verified evidence." — no hallucination, no numeric
    confidence percentages (confidence_state only).
    """
    result = await run_pipeline(
        db,
        body.query,
        product_description=body.product_description,
        standard_number=body.standard_number,
        source_type=body.source_type,
        licence_class=body.licence_class,
    )
    return result


class ComplianceCaseCreate(BaseModel):
    product: str = Field(..., min_length=1, max_length=500)
    standard_ids: list[str] = Field(default_factory=list, max_length=20)
    query: Optional[str] = Field(None, max_length=2000)


@router.post("/compliance-cases")
async def create_compliance_case(
    body: ComplianceCaseCreate,
    db: AsyncSession = Depends(get_db),
):
    """Create a compliance case for a product and its matched standards."""
    # Validate standard ids so the case only links real standards.
    valid_ids: list[str] = []
    for sid in body.standard_ids:
        try:
            uid = uuid.UUID(sid)
        except (ValueError, AttributeError):
            raise HTTPException(
                status_code=422, detail=f"invalid standard id: {sid!r}"
            )
        exists = (
            await db.execute(select(Standard.id).where(Standard.id == uid))
        ).scalar_one_or_none()
        if exists:
            valid_ids.append(str(uid))

    case = ComplianceCase(
        product=body.product.strip(),
        status="open",
        meta={"query": body.query} if body.query else {},
    )
    db.add(case)
    await db.flush()
    for sid in dict.fromkeys(valid_ids):
        await db.execute(
            ComplianceCaseStandard.__table__.insert().values(
                case_id=case.id, standard_id=uuid.UUID(sid)
            )
        )
    await db.commit()
    return {
        "id": str(case.id),
        "product": case.product,
        "status": case.status,
        "standard_ids": valid_ids,
        "created_at": case.created_at.isoformat() if case.created_at else None,
    }


@router.get("/compliance-cases")
async def list_compliance_cases(
    limit: int = Query(50, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: AsyncSession = Depends(get_db),
):
    """List compliance cases with their linked standards."""
    total = (
        await db.execute(select(func.count(ComplianceCase.id)))
    ).scalar_one()
    rows = (
        await db.execute(
            select(ComplianceCase)
            .order_by(ComplianceCase.created_at.desc())
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    cases = []
    for case in rows:
        std_rows = (
            await db.execute(
                select(Standard)
                .join(
                    ComplianceCaseStandard.__table__,
                    ComplianceCaseStandard.__table__.c.standard_id == Standard.id,
                )
                .where(ComplianceCaseStandard.__table__.c.case_id == case.id)
                .order_by(Standard.is_number.asc())
            )
        ).scalars().all()
        cases.append(
            {
                "id": str(case.id),
                "product": case.product,
                "status": case.status,
                "created_at": (
                    case.created_at.isoformat() if case.created_at else None
                ),
                "standards": [
                    {"id": str(s.id), "is_number": s.is_number, "title": s.title}
                    for s in std_rows
                ],
            }
        )
    return {"total": int(total), "limit": limit, "offset": offset, "cases": cases}


@router.get("/compliance-cases/{case_id}")
async def compliance_case_detail(
    case_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
):
    case = (
        await db.execute(
            select(ComplianceCase).where(ComplianceCase.id == case_id)
        )
    ).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Compliance case not found")
    std_rows = (
        await db.execute(
            select(Standard)
            .join(
                ComplianceCaseStandard.__table__,
                ComplianceCaseStandard.__table__.c.standard_id == Standard.id,
            )
            .where(ComplianceCaseStandard.__table__.c.case_id == case.id)
            .order_by(Standard.is_number.asc())
        )
    ).scalars().all()
    return {
        "id": str(case.id),
        "product": case.product,
        "status": case.status,
        "created_at": case.created_at.isoformat() if case.created_at else None,
        "meta": case.meta or {},
        "standards": [
            {"id": str(s.id), "is_number": s.is_number, "title": s.title}
            for s in std_rows
        ],
    }


@router.patch("/compliance-cases/{case_id}")
async def update_compliance_case(
    case_id: uuid.UUID,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Update a compliance case status (open → in_progress → closed)."""
    allowed = {"open", "in_progress", "closed"}
    status = (body.get("status") or "").strip()
    if status not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"'status' must be one of: {', '.join(sorted(allowed))}",
        )
    case = (
        await db.execute(
            select(ComplianceCase).where(ComplianceCase.id == case_id)
        )
    ).scalar_one_or_none()
    if not case:
        raise HTTPException(status_code=404, detail="Compliance case not found")
    case.status = status
    await db.commit()
    return {
        "id": str(case.id),
        "product": case.product,
        "status": case.status,
    }


# -------------------------------------------------- Assistant (streaming)


@router.post("/assistant/query/stream")
async def assistant_query_stream(
    body: AssistantQuery,
    db: AsyncSession = Depends(get_db),
):
    """Server-sent events stream of the assistant's pipeline stages.

    Stage events (``stage``) are emitted as each phase completes, then a
    final ``result`` event carries the identical payload as the non-streaming
    endpoint. Purely a transport difference — grounding rules are unchanged.
    """

    async def event_gen():
        yield _sse({"stage": "understanding", "message": "Understanding the product"})
        try:
            result = await run_pipeline(
                db,
                body.query,
                product_description=body.product_description,
                standard_number=body.standard_number,
                source_type=body.source_type,
                licence_class=body.licence_class,
            )
        except Exception as exc:
            logger.exception("assistant stream failed")
            yield _sse({"stage": "error", "message": "Query failed."})
            return

        stages = [
            ("standards", "Matching applicable standards"),
            ("certification", "Resolving the certification route"),
            ("testing", "Collecting testing requirements"),
            ("laboratories", "Finding recognized laboratories"),
            ("evidence", "Assembling cited evidence"),
        ]
        for stage, message in stages:
            await asyncio.sleep(0.05)
            yield _sse({"stage": stage, "message": message})

        yield _sse_event("result", result)

    return _streaming_response(event_gen())


def _sse(payload: dict) -> str:
    return f"data: {json.dumps(payload, default=str)}\n\n"


def _sse_event(event: str, payload) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _streaming_response(gen):
    from fastapi.responses import StreamingResponse

    return StreamingResponse(
        gen,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
