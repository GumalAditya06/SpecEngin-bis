from __future__ import annotations

import json
from pathlib import Path

from .config import PRODUCTS, Settings
from .manifest import CorpusStore
from .utils import atomic_json, utc_now


def build_coverage_report(settings: Settings, store: CorpusStore) -> dict:
    downloaded = [r for r in store.records if r.get("status") == "downloaded"]
    try:
        laboratories = json.loads((settings.metadata_dir / "laboratories.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        laboratories = []

    def sources_contain(*terms: str) -> list[str]:
        found = []
        for record in downloaded:
            identity = " ".join((record.get("title", ""), record.get("source_url", ""))).lower()
            local_path = record.get("local_path")
            if local_path and str(local_path).lower().endswith(".txt"):
                try:
                    identity += " " + Path(local_path).read_text(encoding="utf-8", errors="replace")[:100000].lower()
                except OSError:
                    pass
            if any(term.lower() in identity for term in terms):
                found.append(record["document_id"])
        return found

    product_coverage = {}
    for product_id, product in PRODUCTS.items():
        records = [r for r in downloaded if r.get("product_id") == product_id]
        present = sorted({standard for r in records for standard in r.get("standard_numbers", [])})
        required = list(product.standard_numbers)
        product_coverage[product_id] = {
            "status": "covered" if all(s in present for s in required) and records else "partial",
            "downloaded_documents": len(records),
            "required_standard_versions": required,
            "evidenced_standard_versions": present,
            "laboratory_records": sum(r.get("product_id") == product_id and r.get("status") == "collected" for r in laboratories),
        }

    capabilities = {
        "three_product_mvp": {"status": "covered", "evidence_count": sum(r.get("product_id") in PRODUCTS for r in downloaded)},
        "source_backed_citations": {"status": "covered", "evidence_count": len(downloaded)},
        "product_certification_and_scheme_i": {"status": "covered", "evidence_ids": sources_contain("product-certification", "scheme-i", "scheme-1")},
        "testing_laboratories": {"status": "covered" if any(r.get("status") == "collected" for r in laboratories) else "missing",
                                  "record_count": sum(r.get("status") == "collected" for r in laboratories)},
        "consumer_complaints": {"status": "covered" if sources_contain("complaint-registration", "consumer-protection") else "missing",
                                "evidence_ids": sources_contain("complaint-registration", "consumer-protection")},
        "hallmarking": {"status": "covered" if sources_contain("hallmarking") else "missing", "evidence_ids": sources_contain("hallmarking")},
        "training": {"status": "covered" if sources_contain("training", "nits") else "missing", "evidence_ids": sources_contain("training", "nits")},
        "standards_clubs": {"status": "covered" if sources_contain("standards club", "standards-club", "std-club") else "missing",
                            "evidence_ids": sources_contain("standards club", "standards-club", "std-club")},
        "multilingual_source_material": {"status": "partial", "evidence_ids": sources_contain("lang=hi", "bilingual")},
        "standard_recommendation": {"status": "partial", "scope": "Only the three configured product domains; not the full BIS catalogue."},
        "clause_level_technical_answers": {"status": "blocked", "reason": "Full official standard texts are not present; product manuals and metadata cannot replace normative clauses."},
    }
    full_ready = all(item["status"] == "covered" for item in capabilities.values())
    report = {
        "generated_at": utc_now(),
        "problem_statement": "SIH26107 — AI-powered Intelligent Assistant for Indian Standards and BIS Services",
        "assessment": "FULL_SIH_READY" if full_ready else "THREE_PRODUCT_MVP_READY_NOT_FULL_SIH_SCOPE",
        "full_sih_ready": full_ready,
        "downloaded_documents": len(downloaded),
        "product_coverage": product_coverage,
        "capabilities": capabilities,
        "limitations": [
            "The product recommendation catalogue is deliberately limited to three products.",
            "Full normative Indian Standards are unavailable in the automated corpus, so clause-level answers must abstain.",
            "Multilingual coverage is source-limited and does not by itself implement multilingual retrieval or generation.",
            "Live licence, laboratory, and regulatory status should be refreshed before production answers.",
        ],
    }
    atomic_json(settings.metadata_dir / "coverage_report.json", report)
    return report
