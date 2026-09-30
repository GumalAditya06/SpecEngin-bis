"""Reranking logic for retrieved chunks."""

from typing import Literal

RankingStrategy = Literal["relevance", "freshness", "authority"]


def rerank(
    results: list[dict],
    query: str,
    strategy: RankingStrategy = "relevance",
    top_k: int = 10,
) -> list[dict]:
    """Rerank retrieved chunks by combining base relevance with strategy-specific bonuses."""
    query_lower = query.lower()
    query_terms = set(query_lower.split())

    for rec in results:
        score = rec["score"]
        chunk_text = (rec["chunk"].text or "")[:500].lower()
        chunk_meta = rec.get("meta", {}) or {}
        doc_title = (rec["document"]["title"] or "").lower() if rec.get("document") else ""

        if strategy == "relevance":
            term_hits = sum(1 for t in query_terms if t in chunk_text or t in doc_title)
            score += term_hits * 0.05
            depth = chunk_meta.get("depth", 99)
            score += max(0, (5 - depth)) * 0.01

        elif strategy == "freshness":
            pass

        elif strategy == "authority":
            licence = rec["source"]["licence_class"] if rec.get("source") else ""
            if licence == "full_text_ok":
                score += 0.1

        rec["score"] = round(min(score, 1.0), 4)

    results.sort(key=lambda r: r["score"], reverse=True)
    return results[:top_k]
