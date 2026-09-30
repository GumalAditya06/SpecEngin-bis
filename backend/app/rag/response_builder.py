"""Structured response generation from retrieved evidence.

Grounding rules enforced here:
- The LLM (when configured) receives ONLY retrieved evidence and a strict
  system prompt; its draft is filtered by an IS-number allow-list guard.
- Without an LLM, answers are extractive — built directly from evidence.
- Insufficient evidence → fixed "Insufficient verified evidence." string;
  no invented content, ever.
"""

import logging
import re
from typing import Optional

from app.core.config import settings

logger = logging.getLogger("specengine-bis.rag.response")

CONFIDENCE_STATES = (
    "VERIFIED",
    "STRONG_EVIDENCE",
    "NEEDS_CLARIFICATION",
    "INSUFFICIENT_EVIDENCE",
)

INSUFFICIENT = "Insufficient verified evidence."

_IS_TOKEN_RE = re.compile(r"\bIS\s*\d+(?::\s*\d{4})?\b", re.IGNORECASE)


def determine_confidence(
    citation_count: int,
    total_chunks: int,
    avg_relevance: float,
    has_standard: bool,
    *,
    ambiguous: bool = False,
) -> str:
    """Determine confidence state from evidence metrics — never a percentage."""
    if ambiguous:
        return "NEEDS_CLARIFICATION"
    if citation_count == 0:
        return "INSUFFICIENT_EVIDENCE"
    if citation_count >= 3 and avg_relevance >= 0.55 and has_standard:
        return "VERIFIED"
    if citation_count >= 2 and avg_relevance >= 0.35:
        return "STRONG_EVIDENCE"
    if citation_count >= 1:
        return "NEEDS_CLARIFICATION"
    return "INSUFFICIENT_EVIDENCE"


def build_response(
    evidence_sections: list[dict],
    citations: list[dict],
    query: str,
    confidence: str,
    clarification_question: Optional[str] = None,
    candidates: Optional[list[dict]] = None,
    grounded_answer: Optional[str] = None,
    attributes: Optional[list[dict]] = None,
    route: Optional[list[dict]] = None,
    laboratories: Optional[list[dict]] = None,
) -> dict:
    """Build the final assistant response structure from evidence.

    ``grounded_answer`` is an optional LLM draft produced by
    :func:`generate_grounded`. It is accepted only after the IS-number
    grounding guard passes; otherwise the extractive answer is used.

    ``attributes`` (extracted product understanding), ``route`` (the
    certification/registration steps) and ``laboratories`` (labs linked to
    the matched standards) are computed upstream in the pipeline and passed
    through verbatim — this builder never invents them.
    """
    candidates = candidates or []

    standards = []
    seen_std: set = set()
    # Candidates carry the DB identity (uuid) for each matched IS number.
    std_id_by_number: dict[str, str] = {
        c["is_number"]: c["standard_id"]
        for c in candidates
        if c.get("is_number") and c.get("standard_id")
    }
    for c in citations:
        sn = c.get("standard_number")
        if sn and sn not in seen_std:
            seen_std.add(sn)
            standards.append({
                "is_number": sn,
                "title": c.get("title"),
                "standard_id": std_id_by_number.get(sn),
                "citation_count": sum(
                    1 for cit in citations if cit.get("standard_number") == sn
                ),
            })

    sections = [
        {
            "heading": es["standard_number"],
            "content": _format_section(es),
            "clause_count": len(es["clauses"]),
        }
        for es in evidence_sections
    ]

    # NOTE: _build_answer consumes the FULL evidence_sections (they carry the
    # per-clause excerpts); the reduced ``sections`` are for the API payload.
    answer = _build_answer(
        evidence_sections, confidence, clarification_question, candidates, grounded_answer
    )

    return {
        "answer": answer,
        "sections": sections,
        "standards": standards,
        "attributes": attributes or [],
        "route": route or [],
        "laboratories": laboratories or [],
        "certification": _extract_certification(evidence_sections),
        "testing": _extract_testing(evidence_sections),
        "next_steps": _build_next_steps(confidence, standards),
        "citations": citations,
        "confidence_state": confidence,
        "clarification_question": clarification_question,
    }


# ------------------------------------------------------------- answer


def _build_answer(
    sections: list[dict],
    confidence: str,
    clarification_question: Optional[str],
    candidates: list[dict],
    grounded_answer: Optional[str],
) -> str:
    if confidence == "INSUFFICIENT_EVIDENCE":
        return INSUFFICIENT

    if confidence == "NEEDS_CLARIFICATION" and clarification_question:
        return clarification_question

    if grounded_answer:
        guarded = _guard_is_numbers(grounded_answer, sections)
        if guarded is not None:
            return guarded
        logger.warning(
            "LLM draft failed IS-number grounding guard; using extractive answer"
        )

    parts = []
    for s in sections:
        heading = s.get("standard_number") or s.get("heading") or ""
        for clause in s.get("clauses", []):
            if clause.get("excerpt"):
                parts.append(
                    f"[{heading} · clause {clause['clause']}] {clause['excerpt']}"
                )
    if not parts:
        return INSUFFICIENT
    return "Based on the retrieved standards:\n\n" + "\n\n".join(parts[:5])


def _guard_is_numbers(answer: str, sections: list[dict]) -> Optional[str]:
    """Reject LLM drafts that cite IS numbers absent from the evidence.

    Returns the (unchanged) answer when grounded, else None so the caller
    falls back to the extractive answer. Standard identifiers must come from
    the retrieval layer — the LLM never gets to introduce its own.
    """
    allowed: set[str] = set()
    for s in sections:
        # evidence sections carry "standard_number"; reduced ones "heading"
        heading = s.get("standard_number") or s.get("heading") or ""
        allowed.update(_IS_TOKEN_RE.findall(heading))
        for clause in s.get("clauses", []):
            allowed.update(_IS_TOKEN_RE.findall(clause.get("excerpt") or ""))

    used = set(_IS_TOKEN_RE.findall(answer))
    return answer if used <= allowed else None


# --------------------------------------------------------- LLM generation


async def generate_grounded(
    query: str,
    sections: list[dict],
    candidates: Optional[list[dict]] = None,
) -> Optional[str]:
    """Call the configured LLM (Google Gemini generateContent API).

    Returns None when no API key is configured or the call fails — callers
    then fall back to the extractive answer. Env-gated via BIS_LLM_API_KEY.
    """
    if not settings.llm_api_key:
        return None

    evidence_blocks = []
    for s in sections[:4]:
        # Sections come from assembler.build_evidence_sections:
        # {standard_number, clauses: [{clause, excerpt, score}], total_excerpts}
        evidence_blocks.append(
            f"STANDARD {s.get('standard_number') or 'Unclassified'}:\n"
            f"{_format_section(s)}"
        )
    if candidates:
        cand_lines = "; ".join(
            f"{c['is_number']} — {c['title']}" for c in candidates[:5]
        )
        evidence_blocks.append(f"DATABASE CANDIDATE STANDARDS: {cand_lines}")
    evidence = "\n\n".join(evidence_blocks)

    system_prompt = (
        "You are a compliance assistant for Bureau of Indian Standards (BIS) "
        "regulations. Answer ONLY from the evidence provided. Never invent IS "
        "numbers, clause numbers, requirements, or regulations. If the "
        "evidence does not contain the answer, reply with exactly: "
        "\"Insufficient verified evidence.\" Quote clause numbers as they "
        "appear in the evidence. Be concise."
    )
    user_prompt = (
        f"Evidence:\n{evidence}\n\nQuestion: {query}\n\n"
        "Answer grounded strictly in the evidence above."
    )

    # Gemini 2.5+/3.x models emit hidden "thinking" tokens that share the
    # maxOutputTokens budget — with a tight cap the draft can come back empty
    # or truncated. Disable thinking (deterministic grounding task) and raise
    # the cap. Older models reject thinkingConfig, so gate on model family.
    gen_config: dict = {
        "temperature": 0.0,
        "maxOutputTokens": 2048,
    }
    if re.match(r"gemini-(2\.5|3\.|\d+\.[5-9])", settings.llm_model):
        gen_config["thinkingConfig"] = {"thinkingBudget": 0}

    try:
        import asyncio

        import httpx

        url = (
            f"{settings.llm_api_url.rstrip('/')}"
            f"/models/{settings.llm_model}:generateContent"
        )
        body = {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [
                {"role": "user", "parts": [{"text": user_prompt}]}
            ],
            "generationConfig": gen_config,
        }
        headers = {"x-goog-api-key": settings.llm_api_key}

        async with httpx.AsyncClient(timeout=settings.llm_timeout_seconds) as client:
            # One retry for transient upstream failures (e.g. Gemini 503
            # "overloaded") — generation is on the hot path of every query.
            last_exc: Exception | None = None
            for attempt in range(2):
                try:
                    resp = await client.post(url, json=body, headers=headers)
                    resp.raise_for_status()
                    payload = resp.json()
                    parts = (
                        payload.get("candidates", [{}])[0]
                        .get("content", {})
                        .get("parts", [])
                    )
                    content = "".join(p.get("text", "") for p in parts)
                    return content.strip() or None
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code < 500:
                        raise  # 4xx = config/auth problem; retrying won't help
                    last_exc = exc
                except (httpx.TransportError, httpx.TimeoutException) as exc:
                    last_exc = exc
                if attempt == 0:
                    await asyncio.sleep(0.8)
            raise last_exc  # type: ignore[misc]
    except Exception as exc:
        logger.warning("LLM generation failed: %s", exc)
        return None


# ------------------------------------------------------ section extraction


def _format_section(es: dict) -> str:
    texts = []
    for clause in es.get("clauses", []):
        if clause.get("excerpt"):
            texts.append(f"[{clause['clause']}] {clause['excerpt']}")
    return "\n".join(texts[:5]) if texts else "No clause text available."


def _extract_certification(sections: list[dict]) -> list[dict]:
    certs = []
    for es in sections:
        for clause in es.get("clauses", []):
            text = (clause.get("excerpt") or "").lower()
            if any(
                kw in text
                for kw in ("certification", "licence", "license", "standard mark")
            ):
                certs.append({
                    "clause": clause.get("clause"),
                    "excerpt": clause.get("excerpt"),
                })
    return certs[:5]


def _extract_testing(sections: list[dict]) -> list[dict]:
    tests = []
    for es in sections:
        for clause in es.get("clauses", []):
            text = (clause.get("excerpt") or "").lower()
            if "test" in text or "testing" in text:
                tests.append({
                    "clause": clause.get("clause"),
                    "excerpt": clause.get("excerpt"),
                })
    return tests[:5]


def _build_next_steps(confidence: str, standards: list[dict]) -> list[str]:
    if confidence == "INSUFFICIENT_EVIDENCE":
        return [
            "Refine your query with more specific terms",
            "Try searching by IS number or product name",
        ]
    if confidence == "NEEDS_CLARIFICATION":
        return [
            "Answer the clarification question above",
            "Or search the standards explorer directly",
        ]
    steps = [
        f"Review the cited standards: {', '.join(s['is_number'] for s in standards)}"
    ]
    steps.append("Verify against the original published document")
    if confidence == "STRONG_EVIDENCE":
        steps.append("Consult the certification scheme for compliance details")
    return steps
