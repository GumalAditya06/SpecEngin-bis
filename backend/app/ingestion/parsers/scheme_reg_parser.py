import re
from io import BytesIO
from typing import List, Optional, Dict, Any
from datetime import datetime
from pdfplumber import PDF


def detect_clause_path(text: str) -> Optional[tuple]:
    """
    Detect clause path at the start of text.
    Returns (path_string, title) or None.
    Examples: '5.2.1 Title', '5.2.1(a) Subtitle'
    """
    pattern = r"^(\d+(?:\.\d+)+(?:\([a-z]\))?)\s+(.+)"
    m = re.match(pattern, text.strip())
    if m:
        path = m.group(1)
        title = m.group(2).strip()
        return path, title
    return None


def build_clause_tree_from_lines(
    lines: List[str],
    document_title: str = "",
) -> List[Dict[str, Any]]:
    """
    Build a clause hierarchy from a list of text lines.
    Each line may start with a clause number like '5.2.1'.
    
    Returns list of clause dicts with:
    - path: e.g., '5.2.1'
    - title: clause heading text
    - depth: number of segments
    - order: sibling order
    """
    clauses = []
    sibling_counter = {}

    for line in lines:
        detected = detect_clause_path(line)
        if not detected:
            continue

        path, title = detected
        depth = path.count(".")

        # Get parent path (prefix)
        parent_path = ".".join(path.split(".")[:-1]) if depth > 1 else None

        # Initialize sibling counter for this path level
        if path not in sibling_counter:
            sibling_counter[path] = 0
        sibling_counter[path] += 1
        order_index = sibling_counter[path]

        clause_entry = {
            "path": path,
            "title": title,
            "depth": depth,
            "order_index": order_index,
            "parent_path": parent_path,
            "text": line,
        }
        clauses.append(clause_entry)

    # Sort by path then order
    clauses.sort(key=lambda c: (c["depth"], c["path"], c["order_index"]))
    return clauses


def extract_text_with_clauses_from_bytes(raw_bytes: bytes) -> Dict[str, Any]:
    """
    Extract text from PDF bytes using pdfplumber, detect clause numbers,
    and build a clause hierarchy.

    Returns:
    - full_text: concatenated text
    - clauses: list of clause dicts with path, title, depth
    - clause_by_path: dict mapping path -> clause info
    """
    full_text_parts = []
    all_lines = []

    with PDF(BytesIO(raw_bytes)) as pdf:
        for page in pdf.pages:
            # Extract text with layout preserved
            text = page.extract_text()
            if text:
                full_text_parts.append(text)

            # Extract words with positions for line reconstruction
            words = page.extract_words(
                x_tolerance=2, y_tolerance=2, keep_blank_chars=False
            )

            # Reconstruct lines from words by y-coordinate
            lines = _reconstruct_lines(words)
            all_lines.extend(lines)

    full_text = "\n".join(full_text_parts)
    clauses = build_clause_tree_from_lines(all_lines)
    clause_by_path = {c["path"]: c for c in clauses}

    return {
        "full_text": full_text,
        "clauses": clauses,
        "clause_by_path": clause_by_path,
        "all_lines": all_lines,
    }


def extract_text_with_clauses(
    pdf_path: str, document_title: str = ""
) -> Dict[str, Any]:
    """
    Extract text from a PDF file path using pdfplumber, detect clause numbers,
    and build a clause hierarchy.

    Returns:
    - full_text: concatenated text
    - clauses: list of clause dicts with path, title, depth
    - clause_by_path: dict mapping path -> clause info
    """
    with open(pdf_path, "rb") as fh:
        raw_bytes = fh.read()
    result = extract_text_with_clauses_from_bytes(raw_bytes)
    if document_title:
        result["clauses"] = build_clause_tree_from_lines(
            result["all_lines"], document_title
        )
        result["clause_by_path"] = {c["path"]: c for c in result["clauses"]}
    return result


def _reconstruct_lines(words: List[Dict]) -> List[str]:
    """
    Reconstruct reading-order lines from pdfplumber word objects.
    Groups words by y-coordinate (baseline), sorts by x-coordinate.
    """
    if not words:
        return []

    # Group words by baseline (rounded to 1px). The previous code grouped
    # by `bottom - top` — the word *height* — which collapsed the whole page
    # into one group and made clause detection impossible.
    y_groups = {}
    for word in words:
        y = round(word["bottom"])
        y_groups.setdefault(y, []).append(word)

    lines = []
    for y in sorted(y_groups.keys()):
        group = y_groups[y]
        # Sort by x position
        group.sort(key=lambda w: w["x0"])
        # Extract text
        line_text = " ".join(w["text"] for w in group if w["text"].strip())
        if line_text.strip():
            lines.append(line_text.strip())

    return lines


class SchemeRegPdfParser:
    """Parser for BIS Scheme Regulation PDFs.
    
    Handles hallmarking regulations, amendments, and scheme documents
    from the BIS website. Returns metadata_only licence class as per CLAUDE.md.
    """
    
    name = "scheme_reg"
    
    async def can_handle(self, url: str, raw_bytes: bytes) -> bool:
        """Check if this parser can handle the given content."""
        url_lower = url.lower()
        return (
            "scheme_reg" in url_lower
            or "hallmarking" in url_lower
            or "registration-scheme" in url_lower
            or "jewellers-registration" in url_lower
        )
    
    async def parse(self, raw_bytes: bytes, url: str) -> dict:
        """Parse scheme regulation PDF and return structured data."""
        if raw_bytes[:4] == b"%PDF":
            # Real PDF — let extraction errors surface instead of silently
            # degrading to mojibake-decoded bytes.
            result = extract_text_with_clauses_from_bytes(raw_bytes)
            text = result["full_text"]
        else:
            # HTML/other scheme pages — same clause extraction, run over
            # tag-stripped lines instead of pdfplumber output.
            text = raw_bytes.decode("utf-8", errors="replace")
            lines = re.sub(r"</?[A-Za-z][^>]*>", "\n", text).splitlines()
            clauses = build_clause_tree_from_lines(lines)
            result = {
                "full_text": text,
                "clauses": clauses,
                "clause_by_path": {c["path"]: c for c in clauses},
            }

        # Extract title from PDF content or URL
        title = self._extract_title(text, url)

        # Build metadata
        metadata = self._extract_metadata(text, url)

        return {
            "title": title,
            "is_number": metadata.get("is_number"),
            "source_url": url,
            "clauses": result.get("clauses", []),
            "clause_by_path": result.get("clause_by_path", {}),
            "full_text": result.get("full_text", text),
            "metadata": metadata,
            "licence_class": "metadata_only",
        }
    
    def _extract_title(self, text: str, url: str) -> str:
        """Extract title from PDF content or URL."""
        # Try to find title in text
        title_match = re.search(
            r"(?:hallmarking|scheme|regulation)[^\n]{0,200}",
            text, re.IGNORECASE
        )
        if title_match:
            return title_match.group(0).strip()
        
        # Fallback to URL-based title
        url_path = url.split("/")[-1].replace(".pdf", "").replace("-", " ").title()
        return url_path or "BIS Scheme Regulation"
    
    def _extract_metadata(self, text: str, url: str) -> dict:
        """Extract metadata from PDF text."""
        # Extract IS numbers
        is_numbers = re.findall(r"IS\s*\d+(?:\s*:\s*\d{4})?", text, re.IGNORECASE)
        
        # Extract amendment dates
        amendment_dates = re.findall(
            r"(?:amendment|amdt)[^\d]*(\d{1,2}[\s/\w]+\d{4})|(\d{1,2}[\s/\w]+\d{4})[^\d]*(?:amendment|amdt)",
            text, re.IGNORECASE
        )
        
        # Extract effective/notification dates
        date_patterns = re.findall(
            r"(?:effective|notification|gazette)[^\d]*(\d{1,2}[\s/\w]+\d{4})|(\d{1,2}[\s/\w]+\d{4})[^\d]*(?:effective|notification|gazette)",
            text, re.IGNORECASE
        )
        
        is_number = is_numbers[0] if is_numbers else None
        
        return {
            "is_number": is_number,
            "amendment_dates": [d for ad in amendment_dates for d in ad if d],
            "date_patterns": [d for dp in date_patterns for d in dp if d],
            "url": url,
        }


# Global parser instance
parser = SchemeRegPdfParser()