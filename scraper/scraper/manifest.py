from __future__ import annotations

import json
from pathlib import Path

from .config import Settings
from .utils import atomic_json, utc_now


class CorpusStore:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.settings.metadata_dir.mkdir(parents=True, exist_ok=True)
        self.manifest_path = self.settings.metadata_dir / "manifest.json"
        self.records: list[dict] = self._read_json(self.manifest_path, [])

    @staticmethod
    def _read_json(path: Path, default):
        try:
            with path.open(encoding="utf-8") as handle:
                return json.load(handle)
        except (FileNotFoundError, json.JSONDecodeError):
            return default

    def get(self, document_id: str) -> dict | None:
        return next((r for r in self.records if r.get("document_id") == document_id), None)

    def by_url(self, url: str) -> dict | None:
        return next((r for r in self.records if r.get("source_url") == url), None)

    def by_sha256(self, sha256: str) -> dict | None:
        return next((r for r in self.records if r.get("sha256") == sha256 and r.get("status") == "downloaded"), None)

    def upsert(self, record: dict, save: bool = True) -> None:
        existing = self.get(record["document_id"])
        if existing is None:
            self.records.append(record)
        else:
            existing.update(record)
        if save:
            self.save()

    def save(self) -> None:
        ordered = sorted(self.records, key=lambda r: (r.get("product_id", ""), r.get("document_id", "")))
        atomic_json(self.manifest_path, ordered)
        jsonl = self.settings.metadata_dir / "manifest.jsonl"
        jsonl.write_text("".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in ordered), encoding="utf-8")
        failures = [r for r in ordered if r.get("status") in {"failed", "discovered_not_downloaded"}]
        atomic_json(self.settings.metadata_dir / "failures.json", failures)

    def log(self, event: dict) -> None:
        path = self.settings.metadata_dir / "crawl_log.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")

    def candidate(self, *, document_id: str, product_id: str, standards: list[str], title: str, source_url: str,
                  discovered_from: str, relevance: str, reason: str, kind: str, resource_type: str,
                  save: bool = True) -> dict:
        existing = self.get(document_id)
        if existing:
            sources = list(dict.fromkeys(existing.get("discovered_from_all", []) + [discovered_from]))
            existing["discovered_from_all"] = sources
            ranks = {"REJECTED": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
            if ranks.get(relevance, 0) > ranks.get(existing.get("relevance", "REJECTED"), 0):
                existing.update({"product_id": product_id, "standard_numbers": standards, "title": title or existing.get("title"),
                                 "document_type": kind, "resource_type": resource_type,
                                 "relevance": relevance, "relevance_reason": reason})
            self.upsert(existing, save=save)
            return existing
        record = {
            "document_id": document_id,
            "product_id": product_id,
            "standard_numbers": standards,
            "title": title or "Untitled BIS resource",
            "document_type": kind,
            "resource_type": resource_type,
            "organization": "Bureau of Indian Standards",
            "source_url": source_url,
            "discovered_from": discovered_from,
            "discovered_from_all": [discovered_from],
            "discovered_at": utc_now(),
            "local_path": None,
            "retrieved_at": None,
            "http_status": None,
            "content_type": None,
            "file_size": None,
            "sha256": None,
            "text_extractable": None,
            "relevance": relevance,
            "relevance_reason": reason,
            "status": "discovered",
        }
        self.upsert(record, save=save)
        return record
