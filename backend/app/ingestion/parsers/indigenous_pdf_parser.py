import re
from typing import List, Optional, Dict, Any
from pdfplumber import PDF


def detect_clause_path(text: str) -> Optional[tuple]:
    r"""
    Detect clause path at the start of text.
    Returns (path_string, title) or None.
    Examples: '5.2.1 Title', '5.2.1(a) Subtitle'
    Pattern: ^\d+(\.\d+)*
    """
    pattern = r"^(\d+(?:\.\d+)+(?:\([a-z]\))?)\s+(.+)"
    m = re.match(pattern, text.strip())
    if m:
        path = m.group(1)
        title = m.group(2).strip()
        return path, title
    return None


def _reconstruct_lines(words: List[Dict]) -> List[str]:
    """
    Reconstruct reading-order lines from pdfplumber word objects.
    Groups words by y-coordinate (baseline), sorts by x-coordinate.
    (Copied from pdf_parser.py for compatibility.)
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
    - order_index: sibling order
    - parent_path: parent clause path
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


def extract_text_with_clauses(pdf_path: str) -> Dict[str, Any]:
    """
    Extract text from PDF using pdfplumber, detect clause numbers,
    and build a clause hierarchy.

    Returns:
    - full_text: concatenated text
    - clauses: list of clause dicts with path, title, depth, parent_path, order_index
    - clause_by_path: dict mapping path -> clause info
    """
    full_text_parts = []
    all_lines = []

    with PDF(pdf_path) as pdf:
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


def _detect_multi_column_layout(page) -> bool:
    """
    Detect if a page has multi-column layout by checking
    the width distribution of words.
    """
    words = page.extract_words(
        x_tolerance=2, y_tolerance=2, keep_blank_chars=False
    )
    if not words:
        return False

    widths = [w["x1"] - w["x0"] for w in words]
    if not widths:
        return False

    # Calculate coefficient of variation
    mean_width = sum(widths) / len(widths)
    if mean_width == 0:
        return False

    variances = [(w - mean_width) ** 2 for w in widths]
    cv = (sum(variances) / len(variances)) ** 0.5 / mean_width

    # If there are two distinct word width clusters, it's multi-column
    return cv > 0.5


def _extract_text_multi_column(page) -> str:
    """
    Extract text from a multi-column page by processing each column separately.
    """
    words = page.extract_words(
        x_tolerance=2, y_tolerance=2, keep_blank_chars=False
    )
    if not words:
        return ""

    # Get page width
    page_width = page.width

    # Find vertical split points - look for gaps in x positions
    xs = sorted([w["x0"] for w in words])

    # Simple approach: split page in half if multi-column detected
    split_point = page_width / 2

    left_words = [w for w in words if w["x0"] < split_point]
    right_words = [w for w in words if w["x0"] >= split_point]

    # Process left column
    left_text = " ".join(sorted([w["text"] for w in left_words if w["text"].strip()], key=lambda t: (w["y0"], w["x0"]))) if left_words else ""

    # Process right column
    right_text = " ".join(sorted([w["text"] for w in right_words if w["text"].strip()], key=lambda t: (w["y0"], w["x0"]))) if right_words else ""

    return left_text + " " + right_text


def extract_indigenous_standard(pdf_path: str) -> Dict[str, Any]:
    """
    Parse an Indigenous Indian Standard PDF (free from BIS).
    Extracts full text and builds clause hierarchy with structural metadata.

    Returns:
    - full_text: full extracted text
    - clauses: list of clause dicts with path, title, depth, parent_path, order_index
    - clause_by_path: dict mapping clause path to clause info
    """
    result = extract_text_with_clauses(pdf_path)

    # If multi-column layout detected, retry with multi-column extraction
    # Re-extract with multi-column handling if needed
    try:
        with PDF(pdf_path) as pdf:
            for page in pdf.pages:
                if _detect_multi_column_layout(page):
                    multi_col_text = _extract_text_multi_column(page)
                    # Rebuild lines from multi-column text
                    words = page.extract_words(
                        x_tolerance=2, y_tolerance=2, keep_blank_chars=False
                    )
                    lines = _reconstruct_lines(words)
                    result["clauses"] = build_clause_tree_from_lines(lines)
                    break
    except Exception:
        pass

    return result