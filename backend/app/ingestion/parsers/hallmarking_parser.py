import re
from typing import List, Optional, Dict, Any
from datetime import datetime

from bs4 import BeautifulSoup
import httpx

from app.ingestion.parsers.pdf_parser import detect_clause_path, build_clause_tree_from_lines


class HallmarkingParser:
    """Parser for BIS Hallmarking documents."""
    
    name = "hallmarking"
    
    # BIS Hallmarking source URLs
    SOURCES = {
        "mandatory_order": "https://www.bis.gov.in/hallmarking-overview/mandatory-hallmarking-order/",
        "faqs": "https://www.bis.gov.in/hallmarking-overview/hallmarking-faqs/",
        "regulations": "https://www.bis.gov.in/the-bureau/bis-act-rules-and-regulations/",
        "guidelines": "https://www.bis.gov.in/hallmarking-overview/guidelines-recognition-operation-a-h-centres/",
        "scheme_brief": "https://www.bis.gov.in/hallmarking-overview/brief-hallmarking-scheme/",
    }
    
    async def can_handle(self, url: str, raw_bytes: bytes) -> bool:
        """Check if this parser can handle the given content."""
        return "bis.gov.in" in url and (
            "hallmark" in url.lower() or "hallmarking" in url.lower()
        )

    async def parse(self, raw_bytes: bytes, url: str) -> dict:
        """Parse hallmarking document and return structured data."""
        text = raw_bytes.decode("utf-8", errors="replace")
        soup = BeautifulSoup(text, "html.parser")
        
        # Determine page type and extract accordingly
        title = self._extract_title(soup)
        
        page_type = self._detect_page_type(soup, url)
        
        result = {
            "url": url,
            "title": title,
            "publisher": "BIS - Bureau of Indian Standards",
            "licence_class": "metadata_only",
            "content_hash": self._content_hash(raw_bytes),
            "metadata": {},
        }
        
        if page_type == "faq":
            result["metadata"] = self._extract_faq_pairs(soup)
        elif page_type == "regulation":
            result["metadata"] = self._extract_regulation_clauses(soup, url)
        elif page_type == "order" or page_type == "guideline" or page_type == "scheme":
            result["metadata"] = self._extract_general_text(soup)
        else:
            result["metadata"] = self._extract_general_text(soup)
        
        return result
    
    def _extract_title(self, soup: BeautifulSoup) -> str:
        """Extract page title."""
        # Try h1 first
        h1 = soup.select_one("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)
        
        # Try title tag
        title_tag = soup.select_one("title")
        if title_tag:
            return title_tag.get_text(strip=True)
        
        return "BIS Hallmarking Document"
    
    def _detect_page_type(self, soup: BeautifulSoup, url: str) -> str:
        """Detect the type of hallmarking page."""
        # Check for FAQ accordion
        accordion = soup.select(".accordion, .faq-accordion, .collapsible, .q-accordion")
        if accordion:
            return "faq"
        
        # Check for QCO details
        qco = soup.select_one(".qco, .quality-control-order, [class*='qco']")
        if qco:
            return "order"
        
        # Check for regulation/clause content
        clauses = soup.select("h2, h3, .clause, .regulation")
        if clauses:
            return "regulation"
        
        # Check for scheme brief
        if "scheme" in url.lower() or "brief" in url.lower():
            return "scheme"
        
        # Check for guidelines
        if "guidelines" in url.lower() or "recognition" in url.lower() or "a&h" in url.lower():
            return "guideline"
        
        # Default to order/frequently used page type
        return "order"
    
    def _extract_faq_pairs(self, soup: BeautifulSoup) -> Dict[str, List[Dict[str, str]]]:
        """Extract question-answer pairs from FAQ accordion pages."""
        qa_pairs = []
        
        # Find all accordion items
        accordion_items = soup.select(".accordion-item, .faq-item, .collapsible-item")
        
        for item in accordion_items:
            question = item.select_one(".accordion-button, .question, .faq-question")
            answer = item.select_one(".accordion-content, .answer, .faq-answer")
            
            if question and answer:
                q_text = question.get_text(strip=True)
                a_text = answer.get_text(strip=True)
                if q_text and a_text:
                    qa_pairs.append({
                        "question": q_text,
                        "answer": a_text,
                    })
        
        # Fallback: find all question-answer pairs without structured accordion.
        # (Plain selectors only — soupsieve raises on `:contains(...)`.)
        if not qa_pairs:
            questions = soup.select(".question, .q-question, h2, h3")
            for q in questions:
                q_text = q.get_text(strip=True)
                if q_text.startswith("Q") or q_text.lower().startswith("what") or q_text.lower().startswith("how"):
                    # Get next sibling or following element as answer
                    answer_elem = q.find_next_sibling()
                    if answer_elem:
                        a_text = answer_elem.get_text(strip=True)
                        if a_text and len(a_text) > 5:
                            qa_pairs.append({
                                "question": q_text,
                                "answer": a_text,
                            })
        
        return {"qa_pairs": qa_pairs}
    
    def _extract_regulation_clauses(self, soup: BeautifulSoup, url: str) -> Dict[str, Any]:
        """Extract regulation clauses from hallmarking regulation pages."""
        # Get all text content
        text = soup.get_text()

        # Try to extract clause hierarchy from headings
        clauses = build_clause_tree_from_lines(
            [
                line.get_text(strip=True)
                for line in soup.select("h1, h2, h3, h4, h5, h6")
                if line.get_text(strip=True)
            ]
        )
        # Extract specific regulation details
        regulation_data = {
            "full_text": text[:10000] if text else "",
            "clauses": clauses,
        }
        
        # Extract amendment dates
        date_patterns = re.findall(
            r"(\d{1,2}\s+\w+\s+\d{4}|amended|revised|notification)\s*[:\s]*(\d{1,2}\s+\w+\s+\d{4})",
            text, re.IGNORECASE
        )
        if date_patterns:
            regulation_data["amendment_dates"] = [{"pattern": p[0] + " " + p[1], "date": p[1]} for p in date_patterns[:5]]
        
        # Extract QCO details
        qco_matches = re.findall(r"QCO[-\s]?\d*", text, re.IGNORECASE)
        if qco_matches:
            regulation_data["qco_details"] = list(set(qco_matches))
        
        return regulation_data
    
    def _extract_general_text(self, soup: BeautifulSoup) -> Dict[str, Any]:
        """Extract general text content from hallmarking pages."""
        text = soup.get_text(separator=" ")
        if len(text) > 10000:
            text = text[:10000]
        
        return {
            "full_text": text,
        }
    
    @staticmethod
    def _content_hash(raw_bytes: bytes) -> str:
        """SHA-256 content hash for change detection."""
        import hashlib
        return hashlib.sha256(raw_bytes).hexdigest()


# Convenience functions for pipeline integration


async def parse_mandatory_order(url: str, raw_bytes: bytes) -> dict:
    """Parse mandatory hallmarking order document."""
    parser = HallmarkingParser()
    return await parser.parse(raw_bytes, url)


async def parse_hallmarking_faqs(url: str, raw_bytes: bytes) -> dict:
    """Parse hallmarking FAQs document."""
    parser = HallmarkingParser()
    return await parser.parse(raw_bytes, url)


async def parse_hallmarking_regulations(url: str, raw_bytes: bytes) -> dict:
    """Parse hallmarking regulations document."""
    parser = HallmarkingParser()
    return await parser.parse(raw_bytes, url)