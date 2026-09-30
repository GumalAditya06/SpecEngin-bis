import re
from io import BytesIO
from typing import List, Optional, Dict, Any
from pdfplumber import PDF

IS_NUMBER_RE = re.compile(r"IS\s*\d+(?:\s*:\s*\d{4})?", re.IGNORECASE)


def detect_clause_path(text: str) -> Optional[tuple]:
    """
    Detect clause path at the start of text.
    Returns (path_string, title) or None.
    Examples: '5.2.1 Title', '5.2.1(a) Subtitle'
    """
    # Fixed pattern: requires at least one .digit segment after first digit
    # Matches: 5.1, 5.2.1, 3.4.2.3, etc.
    pattern = r"^(\d+(?:\.\d+)+)\s+(.+)"
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
        result = extract_text_with_clauses_from_bytes(fh.read())
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


class IndigenousPdfParser:
    """Parser for free ("indigenous") Indian Standard PDFs from BIS.

    licence_class: full_text_ok (free per BIS policy — CLAUDE.md).
    """

    name = "indigenous_pdf"

    async def can_handle(self, url: str, raw_bytes: bytes) -> bool:
        return raw_bytes[:4] == b"%PDF" or url.lower().endswith(".pdf")

    async def parse(self, raw_bytes: bytes, url: str) -> dict:
        if raw_bytes[:4] != b"%PDF":
            raise ValueError(f"expected PDF bytes for indigenous_pdf source {url}")

        result = extract_text_with_clauses_from_bytes(raw_bytes)
        full_text = result["full_text"]

        is_number_match = IS_NUMBER_RE.search(full_text)
        title = next(
            (
                line.strip()
                for line in result["all_lines"][:5]
                if line.strip() and not detect_clause_path(line)
            ),
            url.rsplit("/", 1)[-1],
        )

        return {
            "title": title,
            "is_number": is_number_match.group(0) if is_number_match else None,
            "source_url": url,
            "licence_class": "full_text_ok",
            "full_text": full_text,
            "clauses": result["clauses"],
            "clause_by_path": result["clause_by_path"],
        }