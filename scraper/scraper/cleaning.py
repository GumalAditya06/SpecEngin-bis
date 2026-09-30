from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from pathlib import Path

import fitz

from .config import PRODUCTS, Settings
from .extraction import _valid_pdf_records
from .utils import atomic_json, utc_now


STANDARD_RE = re.compile(r"(?i)\bIS\s*([0-9]{2,6})\s*(?::|\()\s*([0-9]{4})\s*\)?")
CLAUSE_RE = re.compile(r"(?m)^\s*(\d+(?:\.\d+)+)(?:\s|$)")
UNIT_RE = re.compile(
    r"(?i)(?<!\w)\d+(?:\.\d+)?\s*(?:%|°\s*C|V|Hz|kW|W|A|mA|MPa|kPa|Pa|bar|mm|cm|m|kg|g|ml|l|litres?)\b"
)
DATE_RE = re.compile(r"(?<!\d)(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4})(?!\d)")
NUMBER_RE = re.compile(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])")
PAGE_NUMBER_RES = (
    re.compile(r"(?i)^\s*page\s+\d+\s*(?:of\s+\d+)?\s*$"),
    re.compile(r"(?i)^\s*\d+\s*\|\s*p\s*a\s*g\s*e\s*$"),
    re.compile(r"^\s*[-–—]?\s*\d{1,4}\s*[-–—]?\s*$"),
)

PRODUCT_PATTERNS = {
    "mixer": ("electric food-mixer", "electric food mixer", "liquidizer", "centrifugal juicer"),
    "kettle": ("electric kettle", "electric kettles and jugs"),
    "pressure_cooker": ("domestic pressure cooker", "pressure cooker", "pressure cookers"),
    "cables": ("cables quality control", "electric cables", "cable specification", "cables_"),
}


def normalize_standard(number: str, year: str) -> str:
    return f"IS {int(number)}:{year}"


def standards_in(text: str) -> list[str]:
    return list(dict.fromkeys(normalize_standard(number, year) for number, year in STANDARD_RE.findall(text or "")))


def _document_text(record: dict, data_dir: Path) -> tuple[str, str, dict]:
    path = Path(str(record["local_path"]))
    if not path.is_absolute():
        path = data_dir.parent / path
    with fitz.open(path) as pdf:
        metadata = dict(pdf.metadata or {})
        pages = [page.get_text("text") for page in pdf]
    return "\n".join(pages), pages[0] if pages else "", metadata


def _detect_document_title(text: str, pdf_metadata: dict) -> str | None:
    metadata_title = str(pdf_metadata.get("title") or "").strip()
    if metadata_title:
        return metadata_title
    for line in text.splitlines():
        match = re.search(r"(?i)may be called the\s+(.+)", line)
        if match:
            return match.group(1).strip().rstrip(".—- ")
    return None


def _detect_product(text: str, source_path: str) -> tuple[str | None, list[str]]:
    body = text.lower()
    identity = (source_path or "").lower()
    scores: dict[str, int] = {}
    evidence: list[str] = []
    for product, patterns in PRODUCT_PATTERNS.items():
        score = 0
        for pattern in patterns:
            if pattern in body:
                score += 2
            if pattern in identity:
                score += 4
        if score:
            scores[product] = score
    if not scores:
        return None, evidence
    best = max(scores, key=scores.get)
    evidence.append(f"deterministic product signals: {best}={scores[best]}")
    return best, evidence


def _detect_type(text: str, identity: str) -> str | None:
    identity_sample = identity.lower()
    identity_rules = (
        ("qco_amendment", ("quality control (amendment) order", "quality control amendment order")),
        ("qco", ("quality control order",)),
        ("implementation_circular", ("guidelines for implementation", "implementation of revised", "circular")),
        ("extension_order", ("extension order", "extension of implementation")),
        ("product_manual", ("product manual", "product-manual", "pm/ is", "pm/is", "pm-is", "bilingual-pm", "approved-is")),
        ("scheme_information", ("scheme-i", "scheme i", "conformity assessment regulations")),
    )
    for kind, terms in identity_rules:
        if any(term in identity_sample for term in terms):
            return kind
    sample = text[:8000].lower()
    if "quality control (amendment) order" in sample or "quality control amendment order" in sample:
        return "qco_amendment"
    if "quality control order" in sample:
        return "qco"
    if "additional guidelines for scheme" in sample or "conformity assessment regulations" in sample:
        return "scheme_information"
    return None


def verify_metadata(record: dict, data_dir: Path) -> dict:
    full_text, first_page, pdf_metadata = _document_text(record, data_dir)
    title = str(record.get("title") or "")
    path = str(record.get("local_path") or "")
    source_url = str(record.get("source_url") or "")
    manifest_standards = list(record.get("standard_numbers") or [])

    strong_text = "\n".join((title, Path(path).name, first_page))
    strong_standards = standards_in(strong_text)
    all_standards = standards_in(full_text)
    # A manifest standard found anywhere in the document is valid verification
    # evidence, but unrelated standards buried in annexes are not promoted to
    # primary metadata.
    document_standards = list(strong_standards)
    for standard in manifest_standards:
        if standard in all_standards and standard not in document_standards:
            document_standards.append(standard)

    detected_product, product_evidence = _detect_product(full_text[:120000], path + " " + source_url)
    detected_type = _detect_type(full_text, title + " " + path + " " + source_url)
    detected_title = _detect_document_title(full_text, pdf_metadata)
    conflicts: list[dict] = []
    needs_review_reasons: list[str] = []

    if manifest_standards and document_standards and not set(manifest_standards) & set(document_standards):
        conflicts.append({
            "conflict_type": "standard_number_mismatch",
            "manifest_value": manifest_standards,
            "document_value": document_standards,
        })
    elif not manifest_standards and strong_standards:
        conflicts.append({
            "conflict_type": "standard_number_mismatch",
            "manifest_value": [],
            "document_value": strong_standards,
            "reason": "Primary standard detected in document identity/first page but absent from manifest",
        })
    elif manifest_standards and not document_standards:
        needs_review_reasons.append("Manifest standard could not be deterministically confirmed in document text")

    manifest_product = record.get("product_id")
    if manifest_product in PRODUCTS and detected_product and detected_product != manifest_product:
        conflicts.append({
            "conflict_type": "product_mismatch",
            "manifest_value": manifest_product,
            "document_value": detected_product,
        })
        if detected_title and any(term in title.lower() for term in PRODUCTS[manifest_product].keywords if not term.isdigit()):
            conflicts.append({
                "conflict_type": "title_mismatch",
                "manifest_value": title,
                "document_value": detected_title,
            })
    elif manifest_product in PRODUCTS and not detected_product:
        needs_review_reasons.append("Configured product could not be deterministically confirmed")

    manifest_type = record.get("document_type")
    if detected_type and manifest_type not in {detected_type, "unknown"}:
        # Scheme/QCO records often quote Scheme-I; only flag strong, specific
        # type disagreements rather than generic embedded references.
        specific = {"product_manual", "qco", "qco_amendment", "implementation_circular", "extension_order"}
        if detected_type in specific and manifest_type in specific:
            conflicts.append({
                "conflict_type": "document_type_mismatch",
                "manifest_value": manifest_type,
                "document_value": detected_type,
            })

    conflict_types = list(dict.fromkeys(item["conflict_type"] for item in conflicts))
    conflict_type = "multiple_conflicts" if len(conflict_types) > 1 else (conflict_types[0] if conflict_types else None)
    metadata_conflict = bool(conflicts)
    verification_status = "needs_review" if metadata_conflict or needs_review_reasons else "verified"
    return {
        "document_id": record.get("document_id"),
        "product_id": manifest_product,
        "source_url": source_url,
        "source_file": record.get("local_path"),
        "manifest_standard": manifest_standards,
        "document_detected_standard": document_standards,
        "all_document_standard_references": all_standards,
        "manifest_product": manifest_product,
        "document_detected_product": detected_product,
        "manifest_title": title,
        "document_detected_title": detected_title,
        "manifest_document_type": manifest_type,
        "document_detected_type": detected_type,
        "metadata_conflict": metadata_conflict,
        "conflict_type": conflict_type,
        "conflicts": conflicts,
        "verification_status": verification_status,
        "needs_review_reasons": needs_review_reasons,
        "evidence": {
            "sources_inspected": ["manifest", "document_title", "first_page", "source_filename", "source_url", "document_text"],
            "product_signals": product_evidence,
        },
    }


def build_metadata_consistency_report(settings: Settings, store) -> dict:
    records = _valid_pdf_records(store.records)
    documents = [verify_metadata(record, settings.data_dir) for record in records]
    conflicts = [document for document in documents if document["metadata_conflict"]]
    review = [document for document in documents if document["verification_status"] == "needs_review"]
    report = {
        "generated_at": utc_now(),
        "method": "deterministic_regex_and_document_evidence",
        "source_manifest_modified": False,
        "documents_checked": len(documents),
        "documents_without_conflicts": len(documents) - len(conflicts),
        "documents_with_conflicts": len(conflicts),
        "documents_needing_review": len(review),
        "conflicts": conflicts,
        "documents_needing_manual_review": review,
        "documents": documents,
    }
    path = settings.data_dir / "processed" / "metadata" / "metadata_consistency_report.json"
    atomic_json(path, report)
    return report


def _line_key(line: str) -> str:
    return re.sub(r"\s+", " ", line).strip().casefold()


def _safe_repeated_margin_candidate(line: str) -> bool:
    """Only remove repeated margins that do not carry evidentiary values."""
    normalized = re.sub(r"\s+", " ", line).strip()
    if len(normalized) < 4:
        return False
    # A product-manual issue line such as ``PM/IS 4250/4/April 2025`` is a
    # repeated header, but it is also version/date evidence.  Conservation of
    # that evidence takes precedence over cosmetic cleanup.
    if re.search(r"(?i)\bPM\s*/?\s*IS\b", normalized):
        return False
    if STANDARD_RE.search(normalized) or DATE_RE.search(normalized) or UNIT_RE.search(normalized):
        return False
    if CLAUSE_RE.match(normalized):
        return False
    return True


def detect_repeated_marginal_lines(pages: list[dict], edge_lines: int = 4) -> tuple[set[str], set[str]]:
    if len(pages) < 3:
        return set(), set()
    top: Counter = Counter()
    bottom: Counter = Counter()
    for page in pages:
        lines = [line for line in page.get("text", "").splitlines() if line.strip()]
        # Never let top/bottom windows overlap on short pages: an overlapping
        # window can misclassify real repeated content (for example NOTE lines)
        # as both a header and footer.
        window = max(1, min(edge_lines, max(1, (len(lines) - 1) // 2)))
        top.update(set(_line_key(line) for line in lines[:window] if _safe_repeated_margin_candidate(line)))
        bottom.update(set(_line_key(line) for line in lines[-window:] if _safe_repeated_margin_candidate(line)))
    threshold = max(3, math.ceil(len(pages) * 0.8))
    return ({line for line, count in top.items() if count >= threshold},
            {line for line, count in bottom.items() if count >= threshold})


def _is_page_number(line: str) -> bool:
    return any(pattern.fullmatch(line) for pattern in PAGE_NUMBER_RES)


def _is_labelled_page_number(line: str) -> bool:
    return any(pattern.fullmatch(line) for pattern in PAGE_NUMBER_RES[:2])


def _is_heading_or_structure(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if re.match(r"^(?:\d+(?:\.\d+)*[.)]?|[a-zA-Z][.)])\s+", stripped):
        return True
    if re.match(r"(?i)^(?:NOTE|WARNING|CAUTION|ANNEX|TABLE|FIGURE)\b", stripped):
        return True
    letters = [char for char in stripped if char.isalpha()]
    return bool(letters and len(stripped) < 140 and sum(char.isupper() for char in letters) / len(letters) > 0.8)


def _critical_tokens(text: str) -> dict[str, set[str]]:
    return {
        "clauses": set(CLAUSE_RE.findall(text)),
        "standards": set(standards_in(text)),
        "units": {_line_key(value) for value in UNIT_RE.findall(text)},
        "dates": set(DATE_RE.findall(text)),
        "numbers": set(NUMBER_RE.findall(text)),
    }


def clean_page_text(raw_text: str, header_keys: set[str], footer_keys: set[str], edge_lines: int = 4) -> tuple[str, dict, str]:
    raw_text = raw_text.replace("\r\n", "\n").replace("\r", "\n").replace("\u00a0", " ")
    original_lines = raw_text.split("\n")
    nonempty_positions = [index for index, line in enumerate(original_lines) if line.strip()]
    top_positions = set(nonempty_positions[:edge_lines])
    bottom_positions = set(nonempty_positions[-edge_lines:])
    stats: Counter = Counter()
    kept: list[str] = []
    semantic_kept: list[str] = []

    for index, original in enumerate(original_lines):
        key = _line_key(original)
        if key and index in top_positions and key in header_keys:
            stats["headers_removed"] += 1
            continue
        if key and index in bottom_positions and key in footer_keys:
            stats["footers_removed"] += 1
            continue
        labelled_page_number = _is_labelled_page_number(original)
        # A complete labelled line ("Page 2" / "2 | P a g e") is explicit
        # enough to remove even when an address pushes it beyond the edge
        # window. Bare numbers are always retained because they may be table
        # cells or clause content.
        if original.strip() and labelled_page_number:
            stats["page_numbers_removed"] += 1
            continue
        semantic_kept.append(original)
        line = original.strip()
        normalized = re.sub(r"[\t ]+", " ", line)
        normalized = re.sub(r"\s+([,.;!?%])", r"\1", normalized)
        normalized = re.sub(r"([([{])\s+", r"\1", normalized)
        if normalized != line:
            stats["whitespace_normalizations"] += 1
        if not normalized:
            if kept and kept[-1] != "":
                kept.append("")
            elif kept:
                stats["blank_lines_removed"] += 1
            continue
        kept.append(normalized)

    joined: list[str] = []
    index = 0
    while index < len(kept):
        current = kept[index]
        if not current:
            if joined and joined[-1] != "":
                joined.append("")
            index += 1
            continue
        while index + 1 < len(kept) and kept[index + 1]:
            following = kept[index + 1]
            if _is_heading_or_structure(current) or _is_heading_or_structure(following):
                break
            if current.endswith("-") and re.match(r"^[a-z]", following):
                fragment = re.search(r"([A-Za-z]{2,})-$", current)
                if not fragment:
                    break
                current = current[:-1] + following
                stats["dehyphenations"] += 1
                stats["line_joins"] += 1
                index += 1
                continue
            if (len(current) >= 30 and not re.search(r"[.!?:;]$", current)
                    and re.match(r"^[a-z]", following)):
                current += " " + following
                stats["line_joins"] += 1
                index += 1
                continue
            break
        joined.append(current)
        index += 1

    while joined and joined[-1] == "":
        joined.pop()
    cleaned = "\n".join(joined).strip()
    return cleaned, dict(stats), "\n".join(semantic_kept)


def _validate_cleaned_document(source: dict, cleaned_pages: list[dict], semantic_texts: list[str]) -> dict:
    checks = {
        "same_page_count": len(source["pages"]) == len(cleaned_pages),
        "page_numbers_preserved": [p["page_number"] for p in source["pages"]] == [p["page_number"] for p in cleaned_pages],
        "no_page_silently_removed": all("cleaned_text" in page for page in cleaned_pages),
    }
    semantic_all = "\n".join(semantic_texts)
    cleaned_all = "\n".join(page["cleaned_text"] for page in cleaned_pages)
    raw_tokens = _critical_tokens(semantic_all)
    clean_tokens = _critical_tokens(cleaned_all)
    checks.update({
        "clause_numbers_preserved": raw_tokens["clauses"] <= clean_tokens["clauses"],
        "standard_numbers_preserved": raw_tokens["standards"] <= clean_tokens["standards"],
        "units_preserved": raw_tokens["units"] <= clean_tokens["units"],
        "dates_preserved": raw_tokens["dates"] <= clean_tokens["dates"],
        "numerical_values_preserved": raw_tokens["numbers"] <= clean_tokens["numbers"],
        "notes_preserved": len(re.findall(r"(?im)^\s*NOTE\b", cleaned_all)) >= len(re.findall(r"(?im)^\s*NOTE\b", semantic_all)),
        "table_content_not_aggressively_removed": len(cleaned_all) >= int(len(semantic_all.strip()) * 0.70),
    })
    checks["passed"] = all(checks.values())
    return checks


def clean_extracted_document(source_path: Path, output_path: Path, metadata: dict) -> tuple[dict, dict]:
    source_bytes_before = source_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes_before).hexdigest()
    source = json.loads(source_bytes_before)
    headers, footers = detect_repeated_marginal_lines(source["pages"])
    pages: list[dict] = []
    semantic_texts: list[str] = []
    totals: Counter = Counter()
    for page in source["pages"]:
        raw_text = page.get("text", "")
        cleaned_text, stats, semantic_text = clean_page_text(raw_text, headers, footers)
        semantic_texts.append(semantic_text)
        totals.update(stats)
        pages.append({
            "page_number": page["page_number"],
            "raw_text": raw_text,
            "cleaned_text": cleaned_text,
            "character_count_raw": len(raw_text),
            "character_count_cleaned": len(cleaned_text),
            "cleaning_statistics": stats,
        })
    checks = _validate_cleaned_document(source, pages, semantic_texts)
    if not checks["passed"]:
        failed = [name for name, passed in checks.items() if name != "passed" and not passed]
        raise ValueError(f"Cleaning quality checks failed for {source.get('document_id')}: {failed}")
    if source_path.read_bytes() != source_bytes_before:
        raise RuntimeError(f"Raw extraction changed during cleaning: {source_path}")
    provenance = {key: value for key, value in source.items() if key != "pages"}
    result = {
        **provenance,
        "raw_extraction_file": str(source_path),
        "raw_extraction_sha256": source_hash,
        "cleaned_at": utc_now(),
        "manifest_standard_numbers": metadata.get("manifest_standard", []),
        "document_detected_standard_numbers": metadata.get("document_detected_standard", []),
        "metadata_conflict": metadata.get("metadata_conflict", False),
        "conflict_type": metadata.get("conflict_type"),
        "verification_status": metadata.get("verification_status"),
        "repeated_headers_detected": sorted(headers),
        "repeated_footers_detected": sorted(footers),
        "quality_checks": checks,
        "pages": pages,
    }
    atomic_json(output_path, result)
    stats = {
        "document_id": source.get("document_id"),
        "product_id": source.get("product_id"),
        "input_file": str(source_path),
        "output_file": str(output_path),
        "pages_processed": len(pages),
        "raw_character_count": sum(len(page.get("text", "")) for page in source["pages"]),
        "cleaned_character_count": sum(len(page["cleaned_text"]) for page in pages),
        "headers_removed": totals["headers_removed"],
        "footers_removed": totals["footers_removed"],
        "page_numbers_removed": totals["page_numbers_removed"],
        "whitespace_normalizations": totals["whitespace_normalizations"],
        "line_joins": totals["line_joins"],
        "dehyphenations": totals["dehyphenations"],
        "metadata_conflicts": 1 if metadata.get("metadata_conflict") else 0,
        "quality_checks_passed": checks["passed"],
    }
    return result, stats


def run_cleaning(settings: Settings, store, product_id: str | None = None, all_documents: bool = False) -> dict:
    metadata_report = build_metadata_consistency_report(settings, store)
    metadata_by_id = {item["document_id"]: item for item in metadata_report["documents"]}
    extracted_dir = settings.data_dir / "processed" / "extracted"
    candidates = []
    for path in sorted(extracted_dir.glob("*.json")):
        if path.name == "extraction_report.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if product_id and data.get("product_id") != product_id:
            continue
        candidates.append((path, data))
    # At Stage 2.3 the default is deliberately the same three Stage 2.2 test
    # documents. --all means all currently extracted documents, not all PDFs.
    if not all_documents and not product_id:
        allowed = {product_id for product_id, _ in (("mixer", 1), ("kettle", 1), ("pressure_cooker", 1))}
        candidates = [(path, data) for path, data in candidates if data.get("product_id") in allowed]

    output_dir = settings.data_dir / "processed" / "cleaned"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": utc_now(),
        "selection": "all_extracted" if all_documents else product_id or "stage_2_2_test_documents",
        "metadata_documents_checked": metadata_report["documents_checked"],
        "metadata_conflicts": metadata_report["documents_with_conflicts"],
        "documents_cleaned": 0,
        "pages_cleaned": 0,
        "raw_character_count": 0,
        "cleaned_character_count": 0,
        "headers_removed": 0,
        "footers_removed": 0,
        "page_numbers_removed": 0,
        "whitespace_normalizations": 0,
        "documents": [],
    }
    for path, data in candidates:
        document_id = data.get("document_id")
        metadata = metadata_by_id.get(document_id, {
            "metadata_conflict": False, "verification_status": "needs_review",
            "manifest_standard": data.get("standard_numbers", []), "document_detected_standard": [],
        })
        _, stats = clean_extracted_document(path, output_dir / path.name, metadata)
        report["documents"].append(stats)
        report["documents_cleaned"] += 1
        for key in ("pages_processed", "raw_character_count", "cleaned_character_count", "headers_removed",
                    "footers_removed", "page_numbers_removed", "whitespace_normalizations"):
            report["pages_cleaned" if key == "pages_processed" else key] += stats[key]
    atomic_json(output_dir / "cleaning_report.json", report)
    return report
