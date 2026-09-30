import hashlib
import re
from typing import List, Optional, Dict, Any
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from app.ingestion.parsers.pdf_parser import (
    detect_clause_path,
    build_clause_tree_from_lines,
    extract_text_with_clauses_from_bytes,
)


KQCO_URLS = [
    "https://www.bis.gov.in/upcoming-qcos-notified-and-due-for-implementation/",
    "https://chemicals.gov.in/chemicals-quality-control-orders",
]

IS_PATTERN = re.compile(r"IS\s*\d+(?:\s*:\s*\d{4})?", re.IGNORECASE)


class QcoParser:
    """Parser for QCO (Quality Control Order) notifications from BIS and other sources."""
    
    name = "qco"
    
    async def can_handle(self, url: str, raw_bytes: bytes) -> bool:
        """Check if this parser can handle the given URL/content."""
        lower_url = url.lower()
        return (
            "qco" in lower_url
            or "quality-control" in lower_url
            or "bis.gov.in" in lower_url
        )
    
    async def parse(self, raw_bytes: bytes, url: str) -> dict:
        """Parse QCO notification page and return structured data."""
        text = raw_bytes.decode("utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        
        # Extract title
        title = soup.select_one("h1, .page-title, .heading, .qco-title")
        title_text = title.get_text(strip=True) if title else "QCO Notification"
        
        # Extract IS numbers from full text
        is_numbers: List[str] = IS_PATTERN.findall(text)
        
        # Clean text to strip HTML tags for regex searches
        clean_text = re.sub(r"<[^>]+>", " ", text)
        
        # Extract enforcement date from clean text
        enforcement_date: Optional[str] = None
        date_patterns = [
            r"enforcement[^\d]*(\d{1,2}\s+\w+\s+\d{4})",
            r"(\d{1,2}\s+\w+\s+\d{4})[^\d]*enforcement",
            r"effective[^\d]*(\d{1,2}\s+\w+\s+\d{4})",
            r"(\d{1,2}\s+\w+\s+\d{4})[^\d]*effective",
        ]
        for pattern in date_patterns:
            match = re.search(pattern, clean_text, re.IGNORECASE)
            if match:
                enforcement_date = match.group(1) or match.group(0)
                break
        
        # Extract ministry/department from clean text
        ministry_text = None
        for pattern in [
            r"Department[^\n]{0,100}",
            r"Ministry[^\n]{0,100}",
            r"Government of [^\n]{0,100}",
        ]:
            m = re.search(pattern, clean_text, re.IGNORECASE)
            if m:
                ministry_text = m.group(0)[:100].strip()
                # Remove trailing gazette/notification info that may be captured
                ministry_text = re.split(
                    r"\s+(?:Gazette|No|Notification|Ministry|Department)[^\n]*",
                    ministry_text,
                    maxsplit=1,
                    flags=re.IGNORECASE,
                )[0].strip()
                break
        
        # Extract gazette notification number from clean text
        gazette_text = None
        # Pattern 1: "Gazette No."
        m = re.search(r"Gazette\s+No[.:]?\s*[A-Za-z0-9/\-]+", clean_text, re.IGNORECASE)
        if m:
            gazette_text = m.group(0).strip()
        else:
            # Pattern 2: just "Gazette" followed by limited text
            m = re.search(r"Gazette[^\n]{0,100}", clean_text, re.IGNORECASE)
            if m:
                gazette_text = m.group(0)[:100].strip()
        if not gazette_text:
            # Pattern 3: "notification" with limited text
            m = re.search(r"notification[^\n]{0,100}", clean_text, re.IGNORECASE)
            if m:
                gazette_text = m.group(0)[:100].strip()
        
        # Extract product name from table or heading.
        # (Only soupsieve-supported CSS selectors here — `:contains(...)`
        # raises SelectorSyntaxError and would kill the whole parse.)
        product_name: Optional[str] = None
        product_selectors = [
            ".product-name",
            ".item-description",
        ]
        for selector in product_selectors:
            elem = soup.select_one(selector)
            if elem:
                product_name = elem.get_text(strip=True)
                break

        # Fallback: header cell that mentions "product" → next data cell
        if not product_name:
            for th in soup.select("th, thead td"):
                if "product" in th.get_text(strip=True).lower():
                    cell = th.find_next("td")
                    if cell:
                        product_name = cell.get_text(strip=True)
                        break
        
        # If no product found from selectors, try getting text from table rows
        if not product_name:
            table = soup.select_one("table, .qco-table, .notification-table")
            if table:
                rows = table.select("tr")
                if rows:
                    first_row_text = rows[0].get_text(strip=True)
                    # Try to extract product name from first row
                    product_match = re.search(r"[A-Za-z][A-Za-z\s]{5,100}", first_row_text)
                    if product_match:
                        product_name = product_match.group(0).strip()
        
        # Build metadata
        metadata: Dict[str, Any] = {
            "is_numbers": is_numbers,
            "enforcement_date": enforcement_date,
            "publisher": ministry_text,
            "gazette_notification": gazette_text,
        }
        if product_name:
            metadata["product_name"] = product_name
        
        # Compute content hash
        content_hash = hashlib.sha256(raw_bytes).hexdigest()
        
        return {
            "title": title_text,
            "url": url,
            "publisher": ministry_text or "BIS",
            "licence_class": "full_text_ok",
            "content_hash": content_hash,
            "metadata": metadata,
            "raw_text": text[:5000] if len(text) > 5000 else text,
        }