import re
from io import BytesIO
from typing import List, Optional, Dict, Any

import httpx
from bs4 import BeautifulSoup

from app.ingestion.parsers.pdf_parser import _reconstruct_lines


class LabListParser:
    """Parser for BIS recognised laboratory lists."""

    name = "lab_list"

    async def can_handle(self, url: str, raw_bytes: bytes) -> bool:
        return "bis.gov.in/laboratorys" in url or "lab-list" in url.lower()

    async def parse(self, raw_bytes: bytes, url: str) -> dict:
        """Parse lab list page or PDF."""
        if raw_bytes[:4] == b"%PDF":
            # PDF bytes already fetched by the caller — no second download.
            return self._parse_pdf_bytes(raw_bytes, url)

        text = raw_bytes.decode("utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        return self._parse_html_page(url, soup, text)

    def _parse_html_page(
        self, url: str, soup: BeautifulSoup, page_text: str
    ) -> dict:
        """Parse the BIS lab list HTML page.

        Selectors here must be soupsieve-compatible — `:contains(...)`
        raises SelectorSyntaxError and would kill the whole parse.
        """
        labs = []
        lab_codes = []
        is_numbers = set()

        # Look for lab entries in table format or list format.
        # NOTE: rows with <td> cells used to be skipped entirely (the old
        # `if not cells:` guard), so real HTML tables always parsed to zero
        # labs. Every row now goes through the same extraction path.
        rows = soup.select("tr, .lab-entry, .lab-row")

        for row in rows:
            text = row.get_text(" ", strip=True)
            if not text or len(text) < 20:
                continue

            # Extract lab code (7-8 digit number)
            code_match = re.search(r'\b(\d{7,8})\b', text)
            lab_code = code_match.group(1) if code_match else None

            # Extract lab name
            name_match = re.search(r'([A-Za-z][A-Za-z\s&]+(?:Pvt|Ltd|Private|Limited)?)', text)
            lab_name = name_match.group(1).strip() if name_match else text[:50]

            # Extract IS numbers mentioned
            is_matches = re.findall(r'IS\s*\d+(?:\s*:\s*\d{4})?', text, re.IGNORECASE)

            # Extract state
            state_match = re.search(r'(Delhi|Maharashtra|Tamilnadu|Karnataka|Gujarat|Uttar|Haryana|Madhya|Rajasthan|West Bengal|Andhra|Telangana|Kerala|Punjab|Madhya Pradesh)', text)
            state = state_match.group(1) if state_match else None

            if lab_code or lab_name:
                labs.append({
                    "lab_code": lab_code,
                    "lab_name": lab_name,
                    "is_numbers": is_matches,
                    "state": state,
                })
                if lab_code:
                    lab_codes.append(lab_code)
                is_numbers.update(is_matches)

        clean_text = soup.get_text(" ", strip=True)
        title_tag = soup.select_one("title")
        title = (
            title_tag.get_text(strip=True)
            if title_tag
            else "BIS Recognised Laboratories"
        )

        return {
            "title": title,
            "url": url,
            "source_type": "lab_list",
            "licence_class": "full_text_ok",
            "labs": labs,
            "lab_codes": lab_codes,
            "is_numbers": list(is_numbers),
            "full_text": page_text,
            "raw_text": clean_text[:3000],
        }

    def _parse_pdf_bytes(self, raw_bytes: bytes, url: str) -> dict:
        """Parse PDF lab-list bytes with pdfplumber."""
        from pdfplumber import PDF

        with PDF(BytesIO(raw_bytes)) as pdf:
            all_text = []
            all_lines = []

            for page in pdf.pages:
                text = page.extract_text()
                if text:
                    all_text.append(text)

                words = page.extract_words(
                    x_tolerance=2, y_tolerance=2, keep_blank_chars=False
                )
                lines = _reconstruct_lines(words)
                all_lines.extend(lines)

            full_text = "\n".join(all_text)

            # Extract lab information from text
            labs = []
            lab_codes = []
            is_numbers = set()

            # Look for lab codes (7-8 digit numbers)
            for line in all_lines:
                # Extract lab code
                code_match = re.search(r'(\d{7,8})', line)
                if code_match:
                    osl_code = code_match.group(1)
                    lab_codes.append(osl_code)

                    # Extract lab name (preceding text)
                    name_parts = line[:code_match.start()].strip()
                    # Clean up
                    name = re.sub(r'\s+', ' ', name_parts).strip()

                    # Extract IS numbers
                    is_matches = re.findall(r'IS\s*\d+(?:\s*:\s*\d{4})?', line, re.IGNORECASE)
                    is_numbers.update(is_matches)

                    # Determine status
                    status = "active"
                    if "Suspension" in line or "suspended" in line.lower():
                        status = "suspended"
                    elif "revoked" in line.lower():
                        status = "revoked"

                    labs.append({
                        "lab_code": osl_code,
                        "lab_name": name if name else f"Lab {osl_code}",
                        "is_numbers": list(is_matches),
                        "status": status,
                    })

            return {
                "title": "BIS Recognised Laboratories",
                "url": url,
                "source_type": "lab_list",
                "licence_class": "full_text_ok",
                "labs": labs,
                "lab_codes": lab_codes,
                "is_numbers": list(is_numbers),
                "full_text": full_text,
            }
