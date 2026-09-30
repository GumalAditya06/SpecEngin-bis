from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Product


DOCUMENT_HINTS = {
    "qco_amendment": ("quality control amendment", "qco amendment", "amendment order", "(amendment) order"),
    "qco": ("quality control order", "qco"),
    "implementation_circular": ("implementation", "circular", "revised is"),
    "extension_order": ("extension", "last date", "concurrent running"),
    "product_manual": ("product manual", "product-manual", "pm/ is", "pm-is", "bilingual-pm", "approved-is", "scheme of inspection and testing"),
    "certification_faq": ("frequently asked questions", "certification faq"),
    "laboratory_scope": ("laboratory scope", "testing charges", "lims"),
    "scheme_information": ("scheme-i", "scheme i", "scheme-1", "isi mark scheme"),
    "certification_guidance": ("certification process", "guidelines for grant", "product certification", "product specific information", "product specific guidelines", "first licence", "marking fee"),
    "consumer_information": ("hallmarking", "consumer", "complaint redressal", "bis care"),
    "technical_document": ("standards club", "training programme", "overview of nits", "know your standards"),
    "standard": ("preview", "specification"),
}


@dataclass(frozen=True)
class Classification:
    relevance: str
    reason: str
    product_id: str | None
    standard_numbers: tuple[str, ...]
    document_type: str


def _forms(standard: str) -> set[str]:
    match = re.search(r"(\d+)\s*:\s*(\d{4})", standard)
    if not match:
        return {standard.lower()}
    number, year = match.groups()
    return {f"is {number}:{year}", f"is {number} : {year}", f"is {number} ({year})", f"is{number}:{year}"}


def document_type(text: str) -> str:
    lowered = text.lower()
    for kind, hints in DOCUMENT_HINTS.items():
        if any(hint in lowered for hint in hints):
            return kind
    return "unknown"


def classify(url: str, title: str, context: str, products: dict[str, Product], hinted_product: str | None = None, common: bool = False) -> Classification:
    haystack = " ".join((url, title, context)).lower()
    identity = " ".join((url, title)).lower()
    if common:
        kind = document_type(identity)
        if kind == "unknown":
            kind = document_type(context)
        common_patterns = (
            r"\bproduct certification\b", r"\bscheme[-\s–—]*(?:i|1)\b",
            r"\bproduct manuals?\b", r"\btesting facilities\b", r"\bcertification process\b",
            r"\bhallmark(?:ing|ed)?\b", r"\bconsumer protection\b", r"\bcomplaint registration\b",
            r"\btraining programme\b", r"\boverview of nits\b", r"\bstandards clubs?\b",
            r"\bknow your standards\b",
            r"\bstandards\.bis\.gov\.in\b",
        )
        # Site-wide navigation repeats certification terms on nearly every BIS
        # page. Require the URL/title identity itself to carry the signal.
        if any(re.search(pattern, identity) for pattern in common_patterns):
            return Classification("MEDIUM", "Configured common BIS certification source", "common", (), kind)
        return Classification("LOW", "Generic BIS page without a focused certification signal", "common", (), kind)

    matches: list[tuple[int, Product, list[str], list[str]]] = []
    for product in products.values():
        standards = [s for s in product.standard_numbers if any(form in haystack for form in _forms(s))]
        number_hits = []
        for standard in product.standard_numbers:
            number = re.search(r"\d+", standard).group()
            # Bare numbers are ambiguous (table row 367, dates, file sizes).
            # A relevance decision requires an explicit IS prefix here.
            if re.search(rf"\bis\s*:?[\s_-]*{re.escape(number)}(?!\d)", identity):
                number_hits.append(standard)
        keyword_hits = [keyword for keyword in product.keywords if keyword.lower() in haystack and not keyword.isdigit()]
        score = 4 * len(standards) + 2 * bool(number_hits) + 2 * len(keyword_hits)
        if hinted_product == product.id and score:
            score += 1
        matches.append((score, product, standards or number_hits, keyword_hits))
    hinted_match = next((item for item in matches if hinted_product and item[1].id == hinted_product and item[0] > 1), None)
    score, product, standards, keywords = hinted_match or max(matches, key=lambda item: item[0])
    identity_kind = document_type(identity)
    context_kind = document_type(context)
    kind = identity_kind
    if kind == "unknown" and re.search(r"\bis\s*\d+\s*:\s*\d{4}\b", title.lower()):
        kind = "standard"
    if kind == "unknown":
        kind = context_kind
    evidence = []
    if standards:
        evidence.append("standard signal: " + ", ".join(dict.fromkeys(standards)))
    if keywords:
        evidence.append("product keyword: " + ", ".join(keywords[:3]))
    if kind != "unknown":
        evidence.append("document signal: " + kind)
    if score >= 5 or (score >= 4 and kind != "unknown"):
        relevance = "HIGH"
    elif score >= 2:
        relevance = "MEDIUM"
    elif score:
        relevance = "LOW"
    else:
        relevance = "REJECTED"
    reason = "; ".join(evidence) or "No configured product or standard evidence"
    return Classification(relevance, reason, product.id if score else None, tuple(dict.fromkeys(standards)), kind)
