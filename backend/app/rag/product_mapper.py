"""Product → Standard mapping: rules + retrieval hybrid, deterministic.

Candidate standard identifiers always originate from the database (Standards
table matched via rules + retrieval) — never invented by an LLM or by this
module. Ambiguity is surfaced as a clarification question instead of a guess.
"""

import re
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.base import Standard

# Keyword → sector hints. Sector strings live in the standards table; rules
# only *narrow the search*, the DB decides the actual candidates.
KEYWORD_SECTOR_RULES: list[tuple[str, str]] = [
    (r"\bsteel wire (rope|ropes)\b", "Metallurgy"),
    (r"\bwire rope\b", "Metallurgy"),
    (r"\bfire (extinguisher|extinguishers)\b", "Fire Safety"),
    (r"\bwater heater\b", "Electrical Appliances"),
    (r"\bimmersion heater\b", "Electrical Appliances"),
    (r"\b(jewellery|jewelry)\b", "Jewellery"),
]

# Single-word product hints (sector-scoped only, never a standard itself).
KEYWORD_SECTOR_SINGLE: dict[str, str] = {
    "rope": "Metallurgy",
    "ropes": "Metallurgy",
    "steel": "Metallurgy",
    "extinguisher": "Fire Safety",
    "extinguishers": "Fire Safety",
    "heater": "Electrical Appliances",
    "heaters": "Electrical Appliances",
    "television": "Electronics",
    "tvs": "Electronics",
    "tv": "Electronics",
    "led": "Electronics",
    "electronic": "Electronics",
    "electronics": "Electronics",
}

_STOPWORDS = frozenset(
    "a an and are as at be by for from in is it of on or that the to with what which".split()
)

_IS_NUMBER_RE = re.compile(r"\bIS\s*\d+(?::\s*\d{4})?\b", re.IGNORECASE)


def extract_attributes(product_description: str) -> list[dict]:
    """Extract structured attributes from a product description."""
    desc = (product_description or "").lower().strip()
    attributes: list[dict] = []

    for pattern, sector in KEYWORD_SECTOR_RULES:
        if re.search(pattern, desc):
            attributes.append({"attribute": "sector_hint", "value": sector})

    # Single-word hints ("tv", "led", "heater") only count when no stronger
    # multi-word rule already matched — "led television" should not produce
    # two Electronics hints.
    if not any(a["attribute"] == "sector_hint" for a in attributes):
        for token in re.findall(r"[a-z]+", desc):
            sector = KEYWORD_SECTOR_SINGLE.get(token)
            if sector:
                attributes.append({"attribute": "sector_hint", "value": sector})
                break

    is_matches = _IS_NUMBER_RE.findall(product_description or "")
    for m in is_matches:
        attributes.append(
            {"attribute": "is_number", "value": re.sub(r"\s+", " ", m.upper())}
        )

    dept_keywords = ["ministry of steel", "dpiit", "bureau of indian standards", "meity"]
    for dept in dept_keywords:
        if dept in desc:
            attributes.append({"attribute": "department", "value": dept.title()})

    return attributes


def _is_ambiguous(description: str, attributes: list[dict]) -> tuple[bool, Optional[str]]:
    """Heuristic ambiguity detection → (ambiguous, clarification question)."""
    if not description.strip():
        return True, (
            "Which product are you asking about? For example: a steel wire "
            "rope, a portable fire extinguisher, or an electric water heater."
        )

    has_hint = any(
        a["attribute"] in ("sector_hint", "is_number") for a in attributes
    )
    if not has_hint:
        # Vague one-word descriptions without any rule hit.
        words = [
            w for w in re.findall(r"[a-z]+", description.lower())
            if w not in _STOPWORDS
        ]
        if len(words) <= 1:
            return True, (
                "Could you describe the product a bit more — what it is and "
                "what it's used for? For example: “a 6-strand steel wire rope "
                "for hoisting” or “a portable water-type fire extinguisher”."
            )
    return False, None


async def map_product_to_standards(
    db: AsyncSession, product_description: str
) -> dict:
    """Map a product description to candidate standards from the database."""
    description = (product_description or "").strip()
    attributes = extract_attributes(description)
    ambiguous, clarification = _is_ambiguous(description, attributes)

    candidates: list[dict] = []
    fallback_query = description

    if not ambiguous:
        # 1. Direct IS-number attributes → exact standard lookup.
        is_numbers = [
            a["value"] for a in attributes if a["attribute"] == "is_number"
        ]
        if is_numbers:
            rows = (
                (await db.execute(
                    select(Standard).where(
                        Standard.is_number.in_(
                            [n.split(":")[0].strip() for n in is_numbers]
                        )
                    )
                ))
                .scalars()
                .all()
            )
            for std in rows:
                candidates.append({
                    "standard_id": str(std.id),
                    "is_number": std.is_number,
                    "title": std.title,
                    "sector": std.sector,
                    "year": std.year,
                    "match_basis": "is_number",
                })

        # 2. Sector hints → standards in those sectors (ranked by keyword
        #    overlap between the description and title/description).
        if not candidates:
            sectors = [
                a["value"] for a in attributes if a["attribute"] == "sector_hint"
            ]
            if sectors:
                rows = (
                    (await db.execute(
                        select(Standard).where(Standard.sector.in_(sectors))
                    ))
                    .scalars()
                    .all()
                )
                words = {
                    w for w in re.findall(r"[a-z0-9]+", description.lower())
                    if w not in _STOPWORDS and len(w) > 2
                }
                # Short product phrases like "led tv" rely on the sector rule
                # alone; keep sector matches even when the title has no word
                # overlap, but rank keyword overlap first.
                scored = []
                for std in rows:
                    hay = f"{std.title} {std.description or ''}".lower()
                    overlap = sum(1 for w in words if w in hay)
                    scored.append((overlap, std))
                scored.sort(key=lambda pair: pair[0], reverse=True)
                for overlap, std in scored:
                    if overlap == 0 and len(scored) > 1:
                        continue
                    candidates.append({
                        "standard_id": str(std.id),
                        "is_number": std.is_number,
                        "title": std.title,
                        "sector": std.sector,
                        "year": std.year,
                        "match_basis": "sector+keyword",
                    })

        if candidates:
            fallback_query = " ".join(
                [candidates[0]["title"], description]
            )

    return {
        "attributes": attributes,
        "candidates": candidates,
        "ambiguous": ambiguous,
        "clarification_question": clarification,
        "fallback_query": fallback_query,
    }
