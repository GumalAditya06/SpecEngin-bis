from __future__ import annotations

from collections import defaultdict, deque
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .config import COMMON_SEEDS, PRODUCTS, Product, Settings
from .http import FetchError, PoliteClient
from .manifest import CorpusStore
from .relevance import Classification, classify
from .utils import canonical_url, stable_id, utc_now


def _is_pdf(url: str, content_type: str = "") -> bool:
    return urlsplit(url).path.lower().endswith(".pdf") or content_type == "application/pdf"


def _supported_link(url: str) -> bool:
    path = urlsplit(url).path.lower()
    blocked_suffixes = (".xlsx", ".xls", ".doc", ".docx", ".zip", ".rar", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".mp4", ".mp3")
    return not path.endswith(blocked_suffixes)


def _link_context(link) -> str:
    parent = link.find_parent(["tr", "li", "p", "div"])
    return (parent or link).get_text(" ", strip=True)[:1200]


class Discoverer:
    def __init__(self, settings: Settings, store: CorpusStore, client: PoliteClient):
        self.settings = settings
        self.store = store
        self.client = client
        self.domain_counts: dict[str, int] = defaultdict(int)

    def discover(self, selected: list[str], include_common: bool = True) -> dict[str, int]:
        counts = defaultdict(int)
        for product_id in selected:
            product = PRODUCTS[product_id]
            for seed in product.seeds:
                counts[product_id] += self._walk(seed, product, common=False)
                self.store.save()
        if include_common:
            for seed in COMMON_SEEDS:
                counts["common"] += self._walk(seed, None, common=True, depth_limit=0)
                self.store.save()
        return dict(counts)

    def _walk(self, seed: str, product: Product | None, common: bool, depth_limit: int | None = None) -> int:
        queue = deque([(canonical_url(seed), 0, seed, seed)])
        seen: set[str] = set()
        accepted = 0
        while queue and accepted < self.settings.max_documents_per_product:
            url, depth, discovered_from, context = queue.popleft()
            if url in seen or not self.client.approved(url):
                continue
            seen.add(url)
            host = urlsplit(url).hostname or ""
            if self.domain_counts[host] >= self.settings.max_pages_per_domain:
                self.store.log({"timestamp": utc_now(), "event": "limit_reached", "url": url, "limit": "max_pages_per_domain"})
                continue
            if _is_pdf(url):
                result = classify(url, context, context, PRODUCTS, product.id if product else None, common)
                if result.relevance == "REJECTED" and product and url == canonical_url(seed):
                    result = Classification("MEDIUM", "Configured official product seed; content verification required",
                                            product.id, (), "unknown")
                elif common and result.relevance not in {"HIGH", "MEDIUM"} and url == canonical_url(seed):
                    result = Classification("MEDIUM", "Configured official BIS common-service seed; content verification required",
                                            "common", (), "technical_document")
                self._record(url, context, discovered_from, result, "PDF")
                accepted += result.relevance in {"HIGH", "MEDIUM"}
                continue
            try:
                fetched = self.client.get(url)
                self.domain_counts[host] += 1
            except FetchError as exc:
                self._failure(url, discovered_from, product.id if product else "common", str(exc), exc.http_status)
                continue
            if _is_pdf(fetched.url, fetched.content_type):
                result = classify(fetched.url, context, context, PRODUCTS, product.id if product else None, common)
                self._record(fetched.url, context, discovered_from, result, "PDF")
                accepted += result.relevance in {"HIGH", "MEDIUM"}
                continue
            if "html" not in fetched.content_type and not fetched.content.lstrip().lower().startswith(b"<!doctype"):
                self._failure(url, discovered_from, product.id if product else "common", f"unsupported content type {fetched.content_type}")
                continue
            soup = BeautifulSoup(fetched.content, "html.parser")
            title = soup.title.get_text(" ", strip=True) if soup.title else context
            page_text = soup.get_text(" ", strip=True)[:30000]
            result = classify(fetched.url, title, page_text, PRODUCTS, product.id if product else None, common)
            if common and result.relevance not in {"HIGH", "MEDIUM"} and url == canonical_url(seed):
                result = Classification("MEDIUM", "Configured official BIS common-service seed; content verified",
                                        "common", (), result.document_type)
            if result.relevance in {"HIGH", "MEDIUM"}:
                self._record(fetched.url, title, discovered_from, result, "HTML")
                accepted += 1
            if depth >= (self.settings.max_depth if depth_limit is None else depth_limit):
                continue
            for link in soup.find_all("a", href=True):
                target = canonical_url(link["href"], fetched.url)
                if not self.client.approved(target) or not _supported_link(target):
                    continue
                label = " ".join((link.get_text(" ", strip=True), _link_context(link)))
                candidate = classify(target, label, label, PRODUCTS, product.id if product else None, common)
                if candidate.relevance in {"HIGH", "MEDIUM"}:
                    queue.append((target, depth + 1, fetched.url, label))
                elif candidate.relevance == "LOW" and candidate.standard_numbers:
                    self._record(target, link.get_text(" ", strip=True), fetched.url, candidate, "PDF" if _is_pdf(target) else "HTML")
        return accepted

    def _record(self, url, title, discovered_from, result, resource_type):
        product_id = result.product_id or "unknown"
        self.store.candidate(
            document_id=stable_id(url), product_id=product_id, standards=list(result.standard_numbers), title=title,
            source_url=url, discovered_from=discovered_from, relevance=result.relevance, reason=result.reason,
            kind=result.document_type, resource_type=resource_type, save=False,
        )

    def _failure(self, url: str, discovered_from: str, product_id: str, reason: str, http_status: int | None = None):
        record = self.store.candidate(
            document_id=stable_id(url), product_id=product_id, standards=[], title="Inaccessible BIS resource",
            source_url=url, discovered_from=discovered_from, relevance="MEDIUM", reason="Configured official BIS source",
            kind="unknown", resource_type="UNKNOWN",
        )
        record.update({"status": "discovered_not_downloaded", "failure_reason": reason, "http_status": http_status})
        self.store.upsert(record)
