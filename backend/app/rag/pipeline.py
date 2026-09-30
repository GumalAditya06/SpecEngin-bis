"""Main RAG pipeline orchestration: retrieve → rerank → assemble → respond."""

import logging
import re
import uuid
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import CertificationScheme, Laboratory, Test, standard_laboratory
from app.rag import assembler, product_mapper, response_builder, reranker, retriever

logger = logging.getLogger("specengine-bis.rag.pipeline")


# The registration route shown for products under mandatory certification
# (Scheme I / IS marks licence). Schemes/registration for other products may
# differ; the route is only ever shown when a standard matched from the DB.
_DEFAULT_ROUTE = [
    {
        "step": 1,
        "title": "Confirm applicability",
        "detail": (
            "Check the applicable QCO/registration notification to confirm the "
            "product falls under mandatory certification."
        ),
        "actor": "Manufacturer",
    },
    {
        "step": 2,
        "title": "Apply for BIS certification",
        "detail": (
            "File the application with BIS (manually or via the ManakOnline "
            "portal) for the matched Indian Standard."
        ),
        "actor": "Manufacturer → BIS",
    },
    {
        "step": 3,
        "title": "Factory inspection and testing",
        "detail": (
            "BIS verifies the manufacturing setup and the product is tested at "
            "a BIS-recognized laboratory against the matched standard."
        ),
        "actor": "BIS + recognized lab",
    },
    {
        "step": 4,
        "title": "Grant of licence",
        "detail": (
            "On conformity, BIS grants the licence to use the Standard Mark on "
            "the product."
        ),
        "actor": "BIS",
    },
]


# ---------------------------------------------------------------- helpers


def _to_uuid(value):
    """Coerce a str/UUID to uuid.UUID; non-parseable values are dropped (None)."""
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, AttributeError):
        return None


def _looks_like_product(query: str) -> bool:
    """True when the query reads as a product statement.

    Matches phrases like "I manufacture X", "we make X", "X manufacturer".
    Used to route the query through the product mapper so a single natural
    sentence still produces the full journey.
    """
    q = query.lower()
    return any(
        re.search(p, q)
        for p in (
            r"\b(i|we) (manufacture|make|produce|build)\b",
            r"\bmanufactur(er|ers|ing|e)? (of|for)\b",
            r"\b(i|we) (sell|export|import|assemble)\b",
        )
    )


async def _labs_for_standards(
    db: AsyncSession, standard_ids: list[str]
) -> list[dict]:
    """Labs linked to the matched standards + the tests they offer.

    Deterministic DB lookup — never a generated list. Labs are ordered by
    name so the UI is stable across runs.
    """
    if not standard_ids:
        return []
    # UUID column binds require uuid.UUID objects, not their str form.
    uuid_ids = [_to_uuid(sid) for sid in standard_ids]
    rows = (
        await db.execute(
            select(Laboratory, Test)
            .join(standard_laboratory, standard_laboratory.c.laboratory_id == Laboratory.id)
            .outerjoin(Test, Test.laboratory_id == Laboratory.id)
            .where(standard_laboratory.c.standard_id.in_(uuid_ids))
            .order_by(Laboratory.name.asc(), Test.test_name.asc())
        )
    ).all()
    by_lab: dict[str, dict] = {}
    for lab, test in rows:
        entry = by_lab.setdefault(
            str(lab.id),
            {
                "id": str(lab.id),
                "name": lab.name,
                "city": lab.city,
                "state": lab.state,
                "recognition_status": lab.recognition_status,
                "tests": [],
            },
        )
        if test and test.test_name not in entry["tests"]:
            entry["tests"].append(test.test_name)
    return list(by_lab.values())


async def _scheme_for_standards(
    db: AsyncSession, standard_ids: list[str]
) -> Optional[dict]:
    """One certification scheme covering the matched standards, if any."""
    if not standard_ids:
        return None
    row = (
        await db.execute(
            select(CertificationScheme)
            .where(CertificationScheme.standard_id.in_([_to_uuid(s) for s in standard_ids]))
            .order_by(CertificationScheme.created_at.asc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if not row:
        return None
    return {"id": str(row.id), "title": row.title}


async def run_pipeline(
    db: AsyncSession,
    query: str,
    *,
    product_description: Optional[str] = None,
    standard_number: Optional[str] = None,
    source_type: Optional[str] = None,
    licence_class: Optional[str] = None,
) -> dict:
    """Execute the RAG pipeline and return the assistant response payload."""
    search_query = query
    candidates: list[dict] = []
    attributes: list[dict] = []
    ambiguous = False
    clarification: Optional[str] = None

    # 1. Product → standard mapping (rules + DB), when a product is given.
    if product_description or _looks_like_product(query):
        # Treat the raw query as a product description when it reads as one —
        # "I manufacture an LED television in India" — so a single field in
        # the UI still produces the full product journey.
        description = product_description or query
        mapping = await product_mapper.map_product_to_standards(db, description)
        attributes = mapping["attributes"]
        ambiguous = mapping["ambiguous"]
        candidates = mapping["candidates"]
        if ambiguous:
            confidence = "NEEDS_CLARIFICATION"
            return response_builder.build_response(
                evidence_sections=[],
                citations=[],
                query=query,
                confidence=confidence,
                clarification_question=mapping["clarification_question"],
                candidates=candidates,
                attributes=attributes,
            )
        if candidates:
            # retrieval uses the best candidate's identity + the description
            std_number = candidates[0]["is_number"]
            if not standard_number:
                standard_number = std_number
            search_query = mapping["fallback_query"]
        logger.info(
            "product mapping: %d candidates, ambiguous=%s",
            len(candidates), ambiguous,
        )

    # 2. Hybrid retrieval (FTS/keyword + vector + metadata filters).
    raw_results = await retriever.retrieve_hybrid(
        db,
        query=search_query,
        standard_number=standard_number,
        source_type=source_type,
        licence_class=licence_class,
        limit=20,
    )

    if not raw_results:
        return response_builder.build_response(
            evidence_sections=[],
            citations=[],
            query=query,
            confidence="INSUFFICIENT_EVIDENCE",
            clarification_question=clarification,
            candidates=candidates,
        )

    # 3. Rerank.
    ranked = reranker.rerank(raw_results, search_query, strategy="relevance", top_k=10)

    # 4. Assemble evidence + citations.
    citations = assembler.assemble_citations(ranked)
    evidence_sections = assembler.build_evidence_sections(ranked, search_query)

    # 5. Confidence + grounded generation.
    avg_relevance = sum(r["score"] for r in ranked) / len(ranked)
    has_standard = any(r.get("standard") for r in ranked)
    confidence = response_builder.determine_confidence(
        citation_count=len(citations),
        total_chunks=len(ranked),
        avg_relevance=avg_relevance,
        has_standard=has_standard,
        ambiguous=False,
    )

    grounded_answer = None
    if confidence in ("VERIFIED", "STRONG_EVIDENCE"):
        grounded_answer = await response_builder.generate_grounded(
            query, evidence_sections, candidates
        )

    if (
        confidence == "NEEDS_CLARIFICATION"
        and not clarification
        and not candidates
    ):
        # Only ask for more input when nothing matched. A matched product
        # already produced standards/route below — asking again would be
        # worse than answering from the evidence we found.
        clarification = (
            "Could you specify a product name, IS number, or department for "
            "a more targeted search?"
        )

    # 6. Compliance route + laboratories — DB-driven, only when the product
    #    mapped to standards or the evidence is strong. A weak one-chunk
    #    match must not produce an invented-looking journey.
    standard_ids = [c["standard_id"] for c in candidates if c.get("standard_id")]
    if (
        not standard_ids
        and confidence in ("VERIFIED", "STRONG_EVIDENCE")
    ):
        # Fall back to standards surfaced by retrieval (e.g. IS-number lookups
        # with no product description).
        standard_ids = [
            r["standard"]["id"] for r in ranked if r.get("standard")
        ]
    standard_ids = list(dict.fromkeys(standard_ids))

    route: list[dict] = []
    laboratories: list[dict] = []
    if standard_ids:
        route = _DEFAULT_ROUTE
        laboratories = await _labs_for_standards(db, standard_ids)
        scheme = await _scheme_for_standards(db, standard_ids)
        if scheme:
            route = [
                {
                    "step": 1,
                    "title": "Scheme applies",
                    "detail": scheme["title"],
                    "actor": "BIS",
                },
                *_DEFAULT_ROUTE[1:],
            ]

    return response_builder.build_response(
        evidence_sections=evidence_sections,
        citations=citations,
        query=query,
        confidence=confidence,
        clarification_question=clarification,
        candidates=candidates,
        grounded_answer=grounded_answer,
        attributes=attributes,
        route=route,
        laboratories=laboratories,
    )
