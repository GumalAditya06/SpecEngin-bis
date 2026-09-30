import re
from typing import List, Optional, Dict, Any

import httpx
from bs4 import BeautifulSoup


KYS_BASE = "https://www.bis.gov.in/know-your-standard"
KYS_SEARCH = "https://www.bis.gov.in/know-your-standard"

# Matches "IS 1000:2018" and the spaced "IS 1000 : 2018" form used elsewhere.
IS_NUMBER_PATTERN = re.compile(r"IS\s*\d+(?:\s*:\s*\d{4})?", re.IGNORECASE)


BIS_PUBLISHER = "Bureau of Indian Standards"


async def fetch_kys_page(session: "httpx.AsyncClient", query: str = "") -> str:
    """Fetch the Know Your Standards page."""
    params = {}
    if query:
        params["q"] = query
    resp = await session.get(KYS_SEARCH, params=params)
    resp.raise_for_status()
    return resp.text


async def parse_kys_search(html: str) -> List[Dict[str, Any]]:
    """Parse KYS search results HTML."""
    soup = BeautifulSoup(html, "html.parser")
    results = []

    tiles = soup.select(".standard-tile, .is-tile, .result-item")

    for tile in tiles:
        link = tile.select_one("a")
        if not link:
            continue

        title = link.get_text(strip=True) or (
            tile.select_one("h3, h4").get_text(strip=True)
            if tile.select_one("h3, h4")
            else ""
        )
        url = link.get("href") or ""
        if not url:
            continue
        if not url.startswith("http"):
            url = f"https://www.bis.gov.in{url}"

        is_num = IS_NUMBER_PATTERN.search(title)

        results.append({
            "title": title,
            "url": url,
            "is_number": is_num.group(0) if is_num else None,
        })

    return results


class KysParser:
    """Parser for Know Your Standards catalogue."""

    name = "kys_catalogue"

    async def can_handle(self, url: str, raw_bytes: bytes) -> bool:
        return "know-your-standard" in url

    async def parse(self, raw_bytes: bytes, url: str) -> dict:
        """Parse KYS page and return structured data."""
        text = raw_bytes.decode("utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")

        # Extract title from page
        title_match = soup.select_one("title")
        title = title_match.get_text(strip=True) if title_match else "Know Your Standards"

        # Extract IS numbers from page
        is_numbers = IS_NUMBER_PATTERN.findall(text)
        primary_is_number = is_numbers[0] if is_numbers else None

        # Source URL
        source_url = url

        # Licence class is 'metadata_only' for KYS catalogue
        licence_class = "metadata_only"

        # Content hash
        from app.ingestion.pipeline import content_hash
        content_hash_val = content_hash(raw_bytes)

        return {
            "title": title,
            "is_number": primary_is_number,
            "source_url": source_url,
            "publisher": BIS_PUBLISHER,
            "licence_class": licence_class,
            "content_hash": content_hash_val,
        }