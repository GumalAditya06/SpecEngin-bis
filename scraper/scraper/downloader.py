from __future__ import annotations

import os
import tempfile
from collections import Counter
from pathlib import Path
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .config import PRODUCTS, Settings
from .http import FetchError, PoliteClient
from .manifest import CorpusStore
from .relevance import classify
from .utils import slugify, utc_now
from .validation import validate_pdf


class Downloader:
    def __init__(self, settings: Settings, store: CorpusStore, client: PoliteClient):
        self.settings = settings
        self.store = store
        self.client = client
        self.stats = Counter()
        self.downloaded_files: list[str] = []

    def download(self, selected: list[str], retry_failed: bool = False) -> Counter:
        allowed = set(selected) | {"common"}
        for record in list(self.store.records):
            if record.get("product_id") not in allowed:
                continue
            status = record.get("status")
            if status == "downloaded":
                path = record.get("local_path")
                if path and Path(path).exists():
                    print(f"[SKIP] already downloaded: {path}")
                    self.stats["skipped"] += 1
                    continue
            if status in {"failed", "discovered_not_downloaded"} and not retry_failed:
                continue
            if record.get("relevance") not in {"HIGH", "MEDIUM"}:
                self.stats["not_downloaded"] += 1
                continue
            self._download_one(record)
        return self.stats

    def _download_one(self, record: dict) -> None:
        url = record["source_url"]
        try:
            fetched = self.client.get(url)
            record.update({"retrieved_at": utc_now(), "http_status": fetched.status_code, "content_type": fetched.content_type, "file_size": len(fetched.content)})
            is_pdf = fetched.content_type == "application/pdf" or fetched.content.startswith(b"%PDF-") or urlsplit(fetched.url).path.lower().endswith(".pdf")
            if is_pdf:
                self._save_pdf(record, fetched.content)
            elif "html" in fetched.content_type or fetched.content.lstrip().lower().startswith((b"<!doctype", b"<html")):
                self._save_html(record, fetched.content)
            else:
                raise ValueError(f"unsupported content type: {fetched.content_type or 'missing'}")
        except (FetchError, OSError, ValueError) as exc:
            if isinstance(exc, FetchError) and exc.http_status is not None:
                record["http_status"] = exc.http_status
            record.update({"status": "discovered_not_downloaded", "failure_reason": str(exc)})
            self.store.upsert(record)
            self.stats["failed"] += 1
            print(f"[FAIL] {url}: {exc}")

    def _save_pdf(self, record: dict, content: bytes) -> None:
        product_id = record.get("product_id") or "unknown"
        destination_dir = self.settings.data_dir / "raw" / product_id
        destination_dir.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix="bis-", suffix=".pdf", dir=destination_dir)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(content)
            temp_path = Path(temporary)
            validation = validate_pdf(temp_path, self.settings.min_pdf_size)
            record.update({"sha256": validation.sha256, "text_extractable": validation.text_extractable})
            if not validation.valid:
                raise ValueError(validation.reason or "invalid PDF")
            combined = " ".join((record.get("title", ""), validation.extracted_text))
            result = classify(record["source_url"], record.get("title", ""), combined, PRODUCTS,
                              product_id if product_id in PRODUCTS else None, product_id == "common")
            record.update({
                "product_id": result.product_id or product_id,
                "standard_numbers": list(result.standard_numbers) or record.get("standard_numbers", []),
                "document_type": result.document_type,
                "relevance": result.relevance,
                "relevance_reason": result.reason + "; verified against retrieved PDF text",
                "content_type": "application/pdf",
                "resource_type": "PDF",
            })
            if result.relevance not in {"HIGH", "MEDIUM"}:
                record.update({"status": "rejected", "failure_reason": "Retrieved content did not confirm relevance"})
                self.store.upsert(record)
                self.stats["not_downloaded"] += 1
                print(f"[REJECT] {record['source_url']}")
                return
            duplicate = self.store.by_sha256(validation.sha256)
            if duplicate and duplicate["document_id"] != record["document_id"]:
                record.update({"status": "duplicate", "duplicate_of": duplicate["document_id"], "local_path": duplicate.get("local_path")})
                self.store.upsert(record)
                self.stats["duplicates"] += 1
                print(f"[DUPLICATE] {record['source_url']} -> {duplicate.get('local_path')}")
                return
            title = self._best_title(record, validation.extracted_text)
            filename = f"{slugify(title)}-{record['document_id'][:8]}.pdf"
            destination = destination_dir / filename
            os.replace(temp_path, destination)
            record.update({"title": title, "local_path": str(destination), "status": "downloaded", "failure_reason": None})
            self.store.upsert(record)
            self.stats["downloaded"] += 1
            self.downloaded_files.append(str(destination))
            print(f"[DOWNLOADED] {destination}")
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _save_html(self, record: dict, content: bytes) -> None:
        soup = BeautifulSoup(content, "html.parser")
        for tag in soup(["script", "style", "noscript", "svg"]):
            tag.decompose()
        title = soup.title.get_text(" ", strip=True) if soup.title else record.get("title", "BIS web page")
        text = soup.get_text("\n", strip=True)
        result = classify(record["source_url"], title, text[:100000], PRODUCTS,
                          record.get("product_id") if record.get("product_id") in PRODUCTS else None,
                          record.get("product_id") == "common")
        configured_common = (record.get("product_id") == "common" and
                             str(record.get("relevance_reason", "")).startswith("Configured official BIS common-service seed"))
        verified_relevance = "MEDIUM" if configured_common and result.relevance not in {"HIGH", "MEDIUM"} else result.relevance
        verified_reason = ("Configured official BIS common-service seed; content retrieved and stored"
                           if configured_common else result.reason + "; verified against retrieved page text")
        record.update({
            "title": title, "product_id": result.product_id or record.get("product_id"),
            "standard_numbers": list(result.standard_numbers) or record.get("standard_numbers", []),
            "document_type": result.document_type, "resource_type": "HTML", "content_type": "text/html",
            "relevance": verified_relevance, "relevance_reason": verified_reason,
        })
        if verified_relevance not in {"HIGH", "MEDIUM"}:
            record.update({"status": "rejected", "failure_reason": "Retrieved page did not confirm relevance"})
            self.store.upsert(record)
            self.stats["not_downloaded"] += 1
            return
        destination_dir = self.settings.data_dir / "raw" / record["product_id"] / "web"
        destination_dir.mkdir(parents=True, exist_ok=True)
        destination = destination_dir / f"{slugify(title)}-{record['document_id'][:8]}.txt"
        destination.write_text(f"Title: {title}\nSource: {record['source_url']}\nRetrieved: {record['retrieved_at']}\n\n{text}\n", encoding="utf-8")
        from .utils import sha256_file
        record.update({"local_path": str(destination), "file_size": destination.stat().st_size,
                       "sha256": sha256_file(destination), "text_extractable": True,
                       "status": "downloaded", "failure_reason": None})
        self.store.upsert(record)
        self.stats["downloaded"] += 1
        self.downloaded_files.append(str(destination))
        print(f"[DOWNLOADED] {destination}")

    @staticmethod
    def _best_title(record: dict, extracted_text: str) -> str:
        current = " ".join(record.get("title", "").split())
        if (current and not current.lower().startswith(("http://", "https://"))
                and current.lower() not in {"download", "view", "pdf", "untitled bis resource"} and len(current) > 5):
            return current[:180]
        lines = [" ".join(line.split()) for line in extracted_text.splitlines() if len(line.strip()) > 5]
        return (lines[0][:180] if lines else Path(urlsplit(record["source_url"]).path).stem)
