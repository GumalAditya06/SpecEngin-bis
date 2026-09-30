from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from .utils import sha256_file, utc_now


@dataclass
class PDFValidation:
    valid: bool
    reason: str | None
    sha256: str
    text_extractable: bool
    extracted_text: str


def validate_pdf(path: Path, minimum_size: int = 512) -> PDFValidation:
    content = path.read_bytes()
    digest = hashlib.sha256(content).hexdigest()
    if len(content) < minimum_size:
        return PDFValidation(False, f"PDF smaller than {minimum_size} bytes", digest, False, "")
    if not content.startswith(b"%PDF-"):
        return PDFValidation(False, "content does not have a PDF signature", digest, False, "")
    if b"%%EOF" not in content[-8192:]:
        return PDFValidation(False, "PDF has no EOF marker near the end", digest, False, "")
    if shutil.which("pdfinfo"):
        result = subprocess.run(["pdfinfo", str(path)], capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            return PDFValidation(False, "pdfinfo could not open the PDF: " + result.stderr.strip()[:300], digest, False, "")
    text = ""
    if shutil.which("pdftotext"):
        with tempfile.NamedTemporaryFile(suffix=".txt") as output:
            # This remains a lightweight validation/relevance preview rather
            # than Stage 2 processing. Twenty-five pages covers official BIS
            # compendia whose target section is not in the opening pages.
            result = subprocess.run(["pdftotext", "-f", "1", "-l", "25", str(path), output.name], capture_output=True, timeout=60)
            if result.returncode == 0:
                text = Path(output.name).read_text(encoding="utf-8", errors="replace").strip()
    else:
        # Conservative fallback: presence of text operators is only a hint.
        text = "PDF text operators detected" if (b"BT" in content and b"ET" in content) else ""
    return PDFValidation(True, None, digest, bool(text), text[:100000])


def audit_corpus(settings, store) -> dict[str, int]:
    """Reopen and rehash every retained corpus file, updating its record."""
    counts = {"valid": 0, "invalid": 0, "duplicates": 0}
    hashes: dict[str, str] = {}
    from .config import PRODUCTS
    from .relevance import classify
    for record in store.records:
        if record.get("status") != "downloaded":
            continue
        path_value = record.get("local_path")
        path = Path(path_value) if path_value else None
        if not path or not path.is_file():
            record.update({"status": "discovered_not_downloaded", "failure_reason": "Local file missing during validation",
                           "validated_at": utc_now(), "validation_status": "invalid"})
            counts["invalid"] += 1
            continue
        if record.get("resource_type") == "PDF" or path.suffix.lower() == ".pdf":
            result = validate_pdf(path, settings.min_pdf_size)
            digest = result.sha256
            audit_text = result.extracted_text
            record.update({"sha256": digest, "file_size": path.stat().st_size,
                           "text_extractable": result.text_extractable,
                           "validation_status": "valid" if result.valid else "invalid", "validated_at": utc_now()})
            if not result.valid:
                record.update({"status": "discovered_not_downloaded", "failure_reason": result.reason})
                counts["invalid"] += 1
                continue
        else:
            digest = sha256_file(path)
            audit_text = path.read_text(encoding="utf-8", errors="replace")[:100000]
            record.update({"sha256": digest, "file_size": path.stat().st_size, "text_extractable": True,
                           "validation_status": "valid", "validated_at": utc_now()})
        if digest in hashes:
            record["duplicate_content_of"] = hashes[digest]
            counts["duplicates"] += 1
        else:
            record.pop("duplicate_content_of", None)
            hashes[digest] = record["document_id"]
        classification = classify(record.get("source_url", ""), record.get("title", ""), audit_text, PRODUCTS,
                                  record.get("product_id") if record.get("product_id") in PRODUCTS else None,
                                  record.get("product_id") == "common")
        record["document_type"] = classification.document_type
        if classification.standard_numbers:
            record["standard_numbers"] = list(classification.standard_numbers)
        counts["valid"] += 1
    store.save()
    return counts
