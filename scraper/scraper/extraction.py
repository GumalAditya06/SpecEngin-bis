from __future__ import annotations

import json
import re
import shutil
from collections import Counter
from pathlib import Path

import fitz

from .utils import atomic_json, sha256_file, utc_now


PROVENANCE_FIELDS = (
    "document_id",
    "product_id",
    "standard_numbers",
    "title",
    "document_type",
    "organization",
    "source_url",
    "retrieved_at",
    "sha256",
    "validation_status",
    "validated_at",
)

FIRST_RUN_TARGETS = (
    ("mixer", "IS 4250:2025"),
    ("kettle", "IS 367:1993"),
    ("pressure_cooker", "IS 2347:2023"),
)


def text_statistics(text: str) -> tuple[int, int, bool, str]:
    """Return deterministic raw-text statistics without altering the text."""
    character_count = len(text)
    word_count = len(text.split())
    is_empty = not text.strip()
    if is_empty:
        quality = "EMPTY"
    elif len(text.strip()) < 50 or word_count < 8:
        quality = "LOW"
    else:
        quality = "GOOD"
    return character_count, word_count, is_empty, quality


def _ocr_capability() -> tuple[bool, object | None, object | None]:
    if not shutil.which("tesseract"):
        return False, None, None
    try:
        import pytesseract
        from PIL import Image
    except ImportError:
        return False, None, None
    return True, pytesseract, Image


def _ocr_page(page: fitz.Page, pytesseract, image_class) -> str:
    pixmap = page.get_pixmap(matrix=fitz.Matrix(2, 2), colorspace=fitz.csRGB, alpha=False)
    image = image_class.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
    return pytesseract.image_to_string(image)


def _valid_pdf_records(records: list[dict]) -> list[dict]:
    return [
        record
        for record in records
        if record.get("status") == "downloaded"
        and record.get("validation_status") == "valid"
        and str(record.get("local_path") or "").lower().endswith(".pdf")
    ]


def _mentions_standard(record: dict, standard_number: str) -> bool:
    number, year = standard_number.removeprefix("IS ").split(":", 1)
    haystack = " ".join(
        str(record.get(field) or "") for field in ("title", "source_url", "local_path")
    )
    return bool(re.search(rf"(?<!\d){re.escape(number)}(?!\d)", haystack)) and bool(
        re.search(rf"(?<!\d){re.escape(year)}(?!\d)", haystack)
    )


def select_first_run_records(records: list[dict]) -> list[dict]:
    validated = _valid_pdf_records(records)
    selected: list[dict] = []
    for product_id, standard_number in FIRST_RUN_TARGETS:
        candidates = [
            record
            for record in validated
            if record.get("product_id") == product_id
            and record.get("document_type") == "product_manual"
            and standard_number in (record.get("standard_numbers") or [])
        ]
        if len(candidates) > 1:
            candidates = [record for record in candidates if _mentions_standard(record, standard_number)]
        if len(candidates) != 1:
            ids = [record.get("document_id") for record in candidates]
            raise ValueError(
                f"Could not uniquely identify {standard_number} product manual; "
                f"found {len(candidates)} candidates: {ids}"
            )
        selected.append(candidates[0])
    return selected


def select_records(records: list[dict], product_id: str | None, all_documents: bool) -> list[dict]:
    if product_id:
        return [record for record in _valid_pdf_records(records) if record.get("product_id") == product_id]
    if all_documents:
        return _valid_pdf_records(records)
    return select_first_run_records(records)


def _output_name(record: dict) -> str:
    standards = record.get("standard_numbers") or []
    standard = standards[0] if standards else record.get("product_id") or "bis"
    stem = re.sub(r"[^a-z0-9]+", "_", str(standard).lower()).strip("_")
    kind = re.sub(r"[^a-z0-9]+", "_", str(record.get("document_type") or "document").lower()).strip("_")
    return f"{stem}_{kind}_{record['document_id'][:8]}.json"


def extract_document(record: dict, data_dir: Path, output_dir: Path) -> tuple[dict, dict]:
    source_path = Path(str(record["local_path"]))
    if not source_path.is_absolute():
        source_path = data_dir.parent / source_path
    before_hash = sha256_file(source_path)
    if record.get("sha256") and before_hash != record["sha256"]:
        raise ValueError(f"SHA-256 mismatch before extraction: {source_path}")

    ocr_available, pytesseract, image_class = _ocr_capability()
    pages: list[dict] = []
    counters: Counter = Counter()
    with fitz.open(source_path) as pdf:
        pdf_page_count = pdf.page_count
        for page_index in range(pdf_page_count):
            page = pdf.load_page(page_index)
            pymupdf_text = page.get_text("text")
            _, _, _, pymupdf_quality = text_statistics(pymupdf_text)
            ocr_required = pymupdf_quality in {"LOW", "EMPTY"}
            ocr_text: str | None = None
            ocr_error: str | None = None
            text = pymupdf_text
            method = "pymupdf"

            if ocr_required:
                counters["pages_ocr_required"] += 1
                if ocr_available:
                    try:
                        ocr_text = _ocr_page(page, pytesseract, image_class)
                        if len(ocr_text.strip()) > len(pymupdf_text.strip()):
                            text = ocr_text
                            method = "tesseract"
                        counters["pages_ocr_successful"] += 1
                    except Exception as exc:  # one OCR failure must not stop the PDF
                        ocr_error = f"{type(exc).__name__}: {exc}"
                        counters["pages_ocr_failed"] += 1
                else:
                    counters["pages_ocr_failed"] += 1

            character_count, word_count, is_empty, quality = text_statistics(text)
            page_record = {
                "page_number": page_index + 1,
                "text": text,
                "pymupdf_text": pymupdf_text,
                "character_count": character_count,
                "word_count": word_count,
                "is_empty": is_empty,
                "extraction_quality": quality,
                "pymupdf_extraction_quality": pymupdf_quality,
                "extraction_method": method,
                "ocr_required": ocr_required,
                "ocr_available": ocr_available,
            }
            if ocr_required:
                page_record["ocr_text"] = ocr_text
                page_record["ocr_error"] = ocr_error
            pages.append(page_record)
            counters[f"pages_{quality.lower()}"] += 1

    after_hash = sha256_file(source_path)
    if after_hash != before_hash:
        raise RuntimeError(f"Source PDF changed during extraction: {source_path}")
    if len(pages) != pdf_page_count or [page["page_number"] for page in pages] != list(range(1, pdf_page_count + 1)):
        raise RuntimeError(f"Page preservation check failed: {source_path}")

    result = {field: record.get(field) for field in PROVENANCE_FIELDS}
    result.update(
        {
            "local_file": record.get("local_path"),
            "source_file": record.get("local_path"),
            "source_status": record.get("status"),
            "extracted_at": utc_now(),
            "pdf_page_count": pdf_page_count,
            "source_sha256_verified": True,
            "pages": pages,
        }
    )
    output_path = output_dir / _output_name(record)
    atomic_json(output_path, result)
    stats = {
        "document_id": record.get("document_id"),
        "product_id": record.get("product_id"),
        "standard_numbers": record.get("standard_numbers") or [],
        "source_file": record.get("local_path"),
        "output_file": str(output_path.relative_to(data_dir.parent)),
        "status": "successful",
        "pages_processed": len(pages),
        "pages_good": counters["pages_good"],
        "pages_low": counters["pages_low"],
        "pages_empty": counters["pages_empty"],
        "pages_ocr_required": counters["pages_ocr_required"],
        "pages_ocr_successful": counters["pages_ocr_successful"],
        "pages_ocr_failed": counters["pages_ocr_failed"],
        "ocr_available": ocr_available,
    }
    return result, stats


def run_extraction(settings, store, product_id: str | None = None, all_documents: bool = False) -> dict:
    selected = select_records(store.records, product_id, all_documents)
    output_dir = settings.data_dir / "processed" / "extracted"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": utc_now(),
        "data_directory": str(settings.data_dir),
        "output_directory": str(output_dir),
        "selection": "all" if all_documents else product_id or "first_run_product_manuals",
        "corpus_pdf_count": sum(
            str(record.get("local_path") or "").lower().endswith(".pdf") for record in store.records
        ),
        "corpus_validated_pdf_count": len(_valid_pdf_records(store.records)),
        "total_pdfs": len(selected),
        "processed_pdfs": 0,
        "successful_pdfs": 0,
        "failed_pdfs": 0,
        "total_pages": 0,
        "pages_good": 0,
        "pages_low": 0,
        "pages_empty": 0,
        "pages_ocr_required": 0,
        "pages_ocr_successful": 0,
        "pages_ocr_failed": 0,
        "documents": [],
    }
    for record in selected:
        try:
            _, stats = extract_document(record, settings.data_dir, output_dir)
            report["successful_pdfs"] += 1
            for report_key, stats_key in (
                ("total_pages", "pages_processed"),
                ("pages_good", "pages_good"),
                ("pages_low", "pages_low"),
                ("pages_empty", "pages_empty"),
                ("pages_ocr_required", "pages_ocr_required"),
                ("pages_ocr_successful", "pages_ocr_successful"),
                ("pages_ocr_failed", "pages_ocr_failed"),
            ):
                report[report_key] += stats[stats_key]
        except Exception as exc:
            report["failed_pdfs"] += 1
            stats = {
                "document_id": record.get("document_id"),
                "product_id": record.get("product_id"),
                "standard_numbers": record.get("standard_numbers") or [],
                "source_file": record.get("local_path"),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
        report["processed_pdfs"] += 1
        report["documents"].append(stats)
    atomic_json(output_dir / "extraction_report.json", report)
    return report
