"""Evidence assembly and citation generation from retrieved chunks."""

from typing import Optional


def assemble_citations(results: list[dict]) -> list[dict]:
    """Build structured citation objects from retrieved chunk results.

    Citation shape: {source_id, document_id, title, clause, page, url,
    excerpt} plus standard_number/section/licence_class context. Excerpts
    are only present for full_text_ok sources (licence gate).
    """
    citations: list[dict] = []
    seen: set = set()
    for rec in results:
        chunk = rec["chunk"]
        doc = rec["document"]
        src = rec["source"]
        meta = rec.get("meta", {}) or {}

        key = (str(chunk.id), doc["id"] or "")
        if key in seen:
            continue
        seen.add(key)

        excerpt = rec.get("evidence_text")
        citations.append({
            "source_id": src["id"],
            "document_id": doc["id"],
            "title": doc["title"],
            "clause": meta.get("clause"),
            "page": meta.get("page"),
            "url": src["url"],
            "excerpt": (excerpt or "")[:300] or None,
            "standard_number": meta.get("standard_number") or doc.get("is_number"),
            "section": meta.get("section"),
            "licence_class": src.get("licence_class"),
        })
    return citations


def build_evidence_sections(
    results: list[dict], query_terms: Optional[list[str]] = None
) -> list[dict]:
    """Group retrieved chunks into evidence sections by standard.

    Metadata-only chunks are retained as clause markers with ``excerpt``
    None so answers can honestly cite them without exposing full text.
    """
    sections: list[dict] = []
    if not results:
        return sections

    by_standard: dict[str, list[dict]] = {}
    order: list[str] = []
    for rec in results:
        std = rec.get("standard")
        std_num = (
            (std or {}).get("is_number")
            or rec["document"].get("is_number")
            or "Unclassified"
        )
        if std_num not in by_standard:
            by_standard[std_num] = []
            order.append(std_num)
        by_standard[std_num].append(rec)

    for std_num in order:
        recs = by_standard[std_num]
        clauses = []
        for rec in recs:
            text = rec.get("evidence_text")
            clauses.append({
                "clause": rec["meta"].get("clause") or "",
                "excerpt": text[:200] if text else None,
                "score": rec["score"],
            })
        sections.append({
            "standard_number": std_num,
            "clauses": clauses,
            "total_excerpts": sum(1 for c in clauses if c["excerpt"]),
        })

    return sections
