from __future__ import annotations

import re

from bs4 import BeautifulSoup

from .config import LIMS_URLS, PRODUCTS, Settings
from .http import FetchError, PoliteClient
from .utils import atomic_json, utc_now


def _clean(value: str) -> str:
    return " ".join(value.split())


class LIMSCollector:
    def __init__(self, settings: Settings, client: PoliteClient):
        self.settings = settings
        self.client = client

    def collect(self, selected: list[str]) -> list[dict]:
        path = self.settings.metadata_dir / "laboratories.json"
        existing = []
        try:
            import json
            existing = json.loads(path.read_text(encoding="utf-8"))
        except (FileNotFoundError, ValueError):
            pass
        def record_key(r):
            return (r.get("product_id"), r.get("laboratory_id") or r.get("laboratory_name"),
                    r.get("standard_number"), r.get("source_url"))

        by_key = {record_key(r): r for r in existing}
        for product_id in selected:
            url = LIMS_URLS[product_id]
            try:
                fetched = self.client.get(url)
                records = self._parse(fetched.content, product_id, fetched.url)
                if any(record.get("status") == "collected" for record in records):
                    # A successful IS-number query is a full snapshot for this
                    # product. Replace stale rows from earlier parser versions
                    # or prior LIMS states instead of accumulating them.
                    for stale_key in [key for key in by_key if key[0] == product_id]:
                        del by_key[stale_key]
                for record in records:
                    key = record_key(record)
                    by_key[key] = record
            except FetchError as exc:
                key = (product_id, None, None, url)
                by_key[key] = {"product_id": product_id, "source_url": url, "retrieved_at": utc_now(), "status": "discovered_not_downloaded", "failure_reason": str(exc)}
        records = sorted(by_key.values(), key=lambda r: (r.get("product_id", ""), r.get("laboratory_name") or ""))
        atomic_json(path, records)
        return records

    def _parse(self, content: bytes, product_id: str, url: str) -> list[dict]:
        soup = BeautifulSoup(content, "html.parser")
        target_numbers = {re.search(r"\d+", s).group() for s in PRODUCTS[product_id].standard_numbers}
        records = []
        for table in soup.find_all("table"):
            header_section = table.find("thead")
            body_section = table.find("tbody")
            header_row = header_section.find("tr") if header_section else table.find("tr")
            if not header_row:
                continue
            headers = [_clean(cell.get_text(" ", strip=True)).lower() for cell in header_row.find_all(["th", "td"], recursive=False)]
            if not any("lab" in h for h in headers) or not any("standard" in h or "is no" in h for h in headers):
                continue
            rows = body_section.find_all("tr", recursive=False) if body_section else table.find_all("tr", recursive=False)[1:]
            for row in rows:
                # LIMS currently emits malformed closing tags (for example a
                # <td> closed with </th>), so direct-child selection sees only
                # two cells. Keep cells whose nearest table is the outer result
                # table; this excludes nested testing-charge modal cells.
                direct_cells = [cell for cell in row.find_all(["td", "th"])
                                if cell.find_parent("table") is table]
                cells = [_clean(cell.get_text(" ", strip=True)) for cell in direct_cells]
                if len(cells) < 3:
                    continue
                mapping = dict(zip(headers, cells))
                standard = next((v for k, v in mapping.items() if "standard" in k or "is no" in k), "")
                if not any(re.search(rf"(?<!\d){number}(?!\d)", standard) for number in target_numbers):
                    continue
                lab = next((v for k, v in mapping.items() if "lab" in k), "")
                osl_value = next((v for k, v in mapping.items() if "osl" in k), "")
                lab_id_match = re.match(r"\s*(\d{4,})\b", osl_value)
                if lab_id_match:
                    lab = lab.split(lab_id_match.group(1), 1)[0].rstrip(" (\t")
                else:
                    lab = re.split(r"\s+None\s+IS\b", lab, maxsplit=1)[0].strip()
                product = next((v for k, v in mapping.items() if "product" in k or "title" in k), "")
                validity = next((v for k, v in mapping.items() if "valid" in k or "status" in k), "")
                test_details = []
                for nested in row.find_all("table"):
                    nested_head = nested.find("thead")
                    nested_body = nested.find("tbody")
                    if not nested_head or not nested_body:
                        continue
                    nested_headers = [_clean(c.get_text(" ", strip=True)).lower()
                                      for c in nested_head.find("tr").find_all(["th", "td"], recursive=False)]
                    if not any("clause" in h for h in nested_headers):
                        continue
                    for nested_row in nested_body.find_all("tr", recursive=False):
                        values = [_clean(c.get_text(" ", strip=True))
                                  for c in nested_row.find_all(["th", "td"], recursive=False)]
                        detail = {key: value for key, value in zip(nested_headers, values) if key and value}
                        if detail:
                            test_details.append(detail)
                clauses = [next((value for key, value in detail.items() if "clause" in key), "") for detail in test_details]
                records.append({
                    "product_id": product_id,
                    "laboratory_name": re.sub(r"\s*\(\d+\).*?$", "", lab).strip() or None,
                    "laboratory_id": lab_id_match.group(1) if lab_id_match else None,
                    "location": lab.rsplit(",", 1)[-1].strip() if "," in lab else None,
                    "standard_number": standard or None,
                    "testing_capability": product or None,
                    "clause": [clause for clause in clauses if clause],
                    "test_method": test_details,
                    "recognition_status": validity or None,
                    "source_url": url,
                    "retrieved_at": utc_now(),
                    "status": "collected",
                })
        if not records:
            # Preserve traceability if LIMS changed its markup or returned a challenge page.
            records.append({"product_id": product_id, "source_url": url, "retrieved_at": utc_now(),
                            "status": "discovered_not_downloaded", "failure_reason": "No target laboratory rows could be parsed"})
        return records
