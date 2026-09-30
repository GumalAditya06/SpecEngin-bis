from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

from .utils import atomic_json, utc_now


ANNEX_RE = re.compile(r"^ANNEX\s*[-–—]?\s*([A-Z0-9]+)(?:\s*[-–—:]\s*(.+))?$", re.IGNORECASE)
TABLE_RE = re.compile(r"^TABLE\s+([A-Z0-9.-]+)(?:\s*[-–—:]\s*(.*))?$", re.IGNORECASE)
REFERENCE_RE = re.compile(r"^(REFERENCES|BIBLIOGRAPHY|NORMATIVE REFERENCES)$", re.IGNORECASE)
NUMBERED_RE = re.compile(
    r"^(?P<number>(?:\d+(?:\.\d+)*|[A-Z]\.\d+(?:\.\d+)*))(?P<dot>\.)?\s+(?P<title>.+)$"
)
MARKER_RE = re.compile(r"^(?P<number>(?:\d+(?:\.\d+)*|[A-Z]\.\d+(?:\.\d+)*))(?P<dot>\.)?$")
PAGE_RE = re.compile(r"(?i)^page\s+\d+(?:\s+of\s+\d+)?$")
DATE_RE = re.compile(r"^(?:\d{1,2}[./-]\d{1,2}[./-]\d{2,4}|\d{4})$")
STANDARD_REFERENCE_RE = re.compile(r"(?i)^IS(?:/ISO(?:/IEC)?)?\s*[-0-9]+(?:\s*\([^)]*\))?\.?$")
LIST_ANNEX_TERMS = (
    "list of test equipment",
    "list of test equipments",
    "possible tests in a day",
    "possible tests in one day",
)


def _nonempty_rank(tokens: list[dict]) -> dict[int, int]:
    by_page: dict[int, int] = Counter()
    ranks: dict[int, int] = {}
    for index, token in enumerate(tokens):
        if token["text"].strip():
            by_page[token["page_number"]] += 1
            ranks[index] = by_page[token["page_number"]]
    return ranks


def _flatten_pages(pages: list[dict]) -> list[dict]:
    tokens: list[dict] = []
    for page in pages:
        for line_number, line in enumerate(str(page.get("cleaned_text") or "").splitlines(), 1):
            tokens.append(
                {
                    "text": line,
                    "page_number": page["page_number"],
                    "line_number": line_number,
                }
            )
    return tokens


def _next_nonempty(tokens: list[dict], index: int, limit: int = 6) -> tuple[int, str] | None:
    inspected = 0
    for candidate in range(index + 1, len(tokens)):
        if tokens[candidate]["page_number"] != tokens[index]["page_number"]:
            break
        value = tokens[candidate]["text"].strip()
        if value:
            return candidate, value
        inspected += 1
        if inspected >= limit:
            break
    return None


def _annex_context(tokens: list[dict], index: int) -> str:
    values: list[str] = []
    for candidate in range(index + 1, min(len(tokens), index + 14)):
        if tokens[candidate]["page_number"] != tokens[index]["page_number"]:
            break
        value = tokens[candidate]["text"].strip()
        if value:
            values.append(value)
        if len(values) >= 6:
            break
    return " ".join(values).casefold()


def _looks_like_heading(title: str) -> bool:
    value = title.strip()
    if not value or len(value) > 240 or "|" in value:
        return False
    if re.match(r"(?i)^to\s+\d", value):
        return False
    if re.match(r"^\d+(?:\.\d+)?\s*(?:V|Hz|W|A|kg|g|mm|cm|m|%)(?:\s|$)", value, re.IGNORECASE):
        return False
    return True


def _is_explicit_non_clause(line: str) -> bool:
    value = line.strip()
    return bool(
        not value
        or "|" in value
        or PAGE_RE.fullmatch(value)
        or DATE_RE.fullmatch(value)
        or STANDARD_REFERENCE_RE.fullmatch(value)
    )


def _is_known_numeric_content(line: str) -> bool:
    value = line.strip()
    if re.match(r"^\d+(?:\.\d+)?\s+to\s+\d+(?:\.\d+)?$", value, re.IGNORECASE):
        return True
    if re.match(r"^\d+(?:\.\d+)?\s+(?:number|numbers|nos?\.?|units?)$", value, re.IGNORECASE):
        return True
    match = NUMBERED_RE.fullmatch(value)
    if match:
        first = match.group("number").split(".", 1)[0]
        if first.isdigit() and int(first) > 99:
            return True
    return False


def parse_numbered_heading(line: str, next_line: str | None = None, *, table_context: bool = False,
                           list_context: bool = False) -> dict | None:
    """Parse a conservative BIS clause/section candidate.

    Context flags are mandatory suppression signals: numeric cells in tables or
    explicitly list-oriented annexes are content, not structural clauses.
    """
    value = line.strip()
    if table_context or list_context or _is_explicit_non_clause(value):
        return None
    match = NUMBERED_RE.fullmatch(value)
    title: str | None = None
    marker_only = False
    if match:
        number = match.group("number")
        title = match.group("title").strip()
        explicit_dot = bool(match.group("dot"))
    else:
        marker = MARKER_RE.fullmatch(value)
        if not marker or not next_line or not _looks_like_heading(next_line):
            return None
        number = marker.group("number")
        title = next_line.strip()
        explicit_dot = bool(marker.group("dot"))
        marker_only = True
    if not _looks_like_heading(title):
        return None
    if re.match(r"(?i)^(?:IS|Cl\.)\s*\d", title):
        return None
    depth = len(number.split("."))
    # A bare top-level integer needs strong heading syntax. This prevents a
    # measurement such as "4 230 V" from becoming section 4.
    if depth == 1 and not explicit_dot:
        letters = [char for char in title if char.isalpha()]
        heading_case = bool(letters) and (
            sum(char.isupper() for char in letters) / len(letters) >= 0.7
            or (len(title.split()) <= 8 and title[:1].isupper())
        )
        if not heading_case:
            return None
    confidence = "high" if depth > 1 or explicit_dot else "medium"
    if marker_only and depth == 1:
        confidence = "medium"
    return {"number": number, "title": title or None, "confidence": confidence, "marker_only": marker_only}


def _number_parts(number: str) -> tuple[str | None, list[str]]:
    parts = number.split(".")
    if parts[0].isalpha():
        return parts[0].upper(), parts[1:]
    return None, parts


def detect_events(pages: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    tokens = _flatten_pages(pages)
    ranks = _nonempty_rank(tokens)
    events: list[dict] = []
    ambiguous: list[dict] = []
    current_annex: str | None = None
    annex_list_context = False
    table_context = False

    for index, token in enumerate(tokens):
        value = token["text"].strip()
        if not value:
            continue
        annex_match = ANNEX_RE.fullmatch(value)
        # Uppercase ANNEX is explicit. Mixed-case "Annex E" in the middle of
        # a table is a reference, not a new annex; permit it only near page top.
        explicit_annex = value.startswith("ANNEX") or ranks.get(index, 999) <= 3
        if annex_match and explicit_annex and (not table_context or value.startswith("ANNEX")):
            identifier = annex_match.group(1).upper()
            context = _annex_context(tokens, index)
            annex_list_context = any(term in context for term in LIST_ANNEX_TERMS)
            current_annex = identifier
            table_context = False
            title = annex_match.group(2)
            if not title:
                following = _next_nonempty(tokens, index)
                title = following[1] if following else None
            events.append(
                {
                    "index": index,
                    "kind": "annex",
                    "identifier": identifier,
                    "title": title,
                    "confidence": "high",
                    "page_number": token["page_number"],
                    "line_number": token["line_number"],
                    "list_context": annex_list_context,
                }
            )
            continue

        table_match = TABLE_RE.fullmatch(value)
        if table_match:
            table_context = True
            events.append(
                {
                    "index": index,
                    "kind": "table",
                    "identifier": table_match.group(1),
                    "title": table_match.group(2),
                    "confidence": "high",
                    "page_number": token["page_number"],
                    "line_number": token["line_number"],
                    "annex_identifier": current_annex,
                }
            )
            continue

        reference_match = REFERENCE_RE.fullmatch(value)
        if reference_match and not table_context:
            events.append(
                {
                    "index": index,
                    "kind": "references",
                    "title": value,
                    "confidence": "high",
                    "page_number": token["page_number"],
                    "line_number": token["line_number"],
                }
            )
            continue

        following = _next_nonempty(tokens, index)
        candidate = parse_numbered_heading(
            value,
            following[1] if following else None,
            table_context=table_context,
            list_context=annex_list_context,
        )
        if candidate:
            letter, number_parts = _number_parts(candidate["number"])
            if letter and current_annex and letter != current_annex:
                ambiguous.append(
                    {
                        "page_number": token["page_number"],
                        "line_number": token["line_number"],
                        "text": value,
                        "reason": "annex_identifier_mismatch",
                    }
                )
                continue
            depth = len(number_parts)
            if current_annex:
                kind = "clause" if depth <= 1 else "subclause"
            else:
                kind = "section" if depth <= 1 else ("clause" if depth == 2 else "subclause")
            events.append(
                {
                    "index": index,
                    "kind": kind,
                    "number": candidate["number"],
                    "title": candidate["title"],
                    "confidence": candidate["confidence"],
                    "page_number": token["page_number"],
                    "line_number": token["line_number"],
                    "annex_identifier": current_annex,
                }
            )
        elif (
            not table_context
            and not annex_list_context
            and (NUMBERED_RE.fullmatch(value) or MARKER_RE.fullmatch(value))
            and not _is_explicit_non_clause(value)
            and not _is_known_numeric_content(value)
        ):
            ambiguous.append(
                {
                    "page_number": token["page_number"],
                    "line_number": token["line_number"],
                    "text": value,
                    "reason": "numbered_line_not_confidently_structural",
                }
            )
    return events, ambiguous, tokens


def _segment(tokens: list[dict], start: int, end: int) -> tuple[str, list[int], list[dict]]:
    selected = tokens[start:end]
    pages = list(dict.fromkeys(token["page_number"] for token in selected if token["text"].strip()))
    page_segments: list[dict] = []
    for page_number in pages:
        text = "\n".join(token["text"] for token in selected if token["page_number"] == page_number).strip()
        page_segments.append({"page_number": page_number, "text": text})
    return "\n".join(token["text"] for token in selected).strip(), pages, page_segments


def _node_from_event(event: dict, tokens: list[dict], end: int) -> dict:
    text, pages, segments = _segment(tokens, event["index"], end)
    node = {
        "type": event["kind"],
        "title": event.get("title"),
        "confidence": event["confidence"],
        "start_page": event["page_number"],
        "end_page": pages[-1] if pages else event["page_number"],
        "pages": pages or [event["page_number"]],
        "source_start": {"page_number": event["page_number"], "line_number": event["line_number"]},
        "text": text,
        "page_segments": segments,
        "children": [],
    }
    if "number" in event:
        node["number"] = event["number"]
    if event["kind"] in {"annex", "table"}:
        node["identifier"] = event["identifier"]
    if event["kind"] == "annex":
        node["content_mode"] = "list_or_table" if event.get("list_context") else "narrative_or_mixed"
    return node


def _is_parent_number(parent: str, child: str) -> bool:
    return child.startswith(parent + ".") and len(child.split(".")) == len(parent.split(".")) + 1


def build_hierarchy(pages: list[dict]) -> tuple[list[dict], list[dict], list[dict]]:
    events, ambiguous, tokens = detect_events(pages)
    structure: list[dict] = []
    if not events:
        text, represented_pages, segments = _segment(tokens, 0, len(tokens))
        return ([{"type": "front_matter", "title": None, "confidence": "high",
                  "start_page": represented_pages[0] if represented_pages else 1,
                  "end_page": represented_pages[-1] if represented_pages else 1,
                  "pages": represented_pages, "text": text, "page_segments": segments, "children": []}],
                ambiguous, events)
    if events[0]["index"] > 0:
        text, represented_pages, segments = _segment(tokens, 0, events[0]["index"])
        structure.append(
            {
                "type": "front_matter",
                "title": None,
                "confidence": "high",
                "start_page": represented_pages[0] if represented_pages else pages[0]["page_number"],
                "end_page": represented_pages[-1] if represented_pages else pages[0]["page_number"],
                "pages": represented_pages,
                "text": text,
                "page_segments": segments,
                "children": [],
            }
        )

    nodes: list[dict] = []
    for position, event in enumerate(events):
        end = events[position + 1]["index"] if position + 1 < len(events) else len(tokens)
        nodes.append(_node_from_event(event, tokens, end))

    current_annex: dict | None = None
    current_section: dict | None = None
    numeric_nodes: list[dict] = []
    for node in nodes:
        if node["type"] == "annex":
            structure.append(node)
            current_annex = node
            current_section = None
            numeric_nodes = []
            continue
        if node["type"] == "references":
            structure.append(node)
            current_annex = None
            current_section = None
            numeric_nodes = []
            continue
        if node["type"] == "table":
            parent = current_annex or current_section
            (parent["children"] if parent else structure).append(node)
            numeric_nodes = []
            continue
        if node["type"] == "section":
            structure.append(node)
            current_section = node
            current_annex = None
            numeric_nodes = [node]
            continue

        parent = None
        child_number = node.get("number", "")
        for candidate in reversed(numeric_nodes):
            if candidate.get("number") and _is_parent_number(candidate["number"], child_number):
                parent = candidate
                break
        if parent is None:
            parent = current_annex or current_section
            if "." in child_number and parent is not None:
                node["confidence"] = "medium"
                node["hierarchy_note"] = "immediate_numbered_parent_not_detected"
                ambiguous.append(
                    {
                        "page_number": node["start_page"],
                        "line_number": node["source_start"]["line_number"],
                        "text": child_number,
                        "reason": "missing_numbered_parent",
                    }
                )
        (parent["children"] if parent else structure).append(node)
        numeric_nodes = [candidate for candidate in numeric_nodes if not (
            candidate.get("number") == child_number or candidate.get("number", "").startswith(child_number + ".")
        )]
        numeric_nodes.append(node)

    def extend_spans(node: dict) -> None:
        for child in node.get("children", []):
            extend_spans(child)
        all_pages = list(node.get("pages", []))
        for child in node.get("children", []):
            all_pages.extend(child.get("pages", []))
        node["pages"] = sorted(set(all_pages))
        if node["pages"]:
            node["start_page"] = min(node["pages"])
            node["end_page"] = max(node["pages"])

    for root in structure:
        extend_spans(root)
    return structure, ambiguous, events


def _walk(nodes: list[dict]):
    for node in nodes:
        yield node
        yield from _walk(node.get("children", []))


def validate_structure(source: dict, result: dict) -> dict:
    source_pages = source.get("pages") or []
    result_pages = result.get("pages") or []
    nodes = list(_walk(result.get("structure") or []))
    valid_pages = {page["page_number"] for page in source_pages}
    represented = set()
    hierarchy_valid = True
    numbers_present = True
    tables_without_clause_children = True
    for node in nodes:
        represented.update(node.get("pages") or [])
        if not set(node.get("pages") or []) <= valid_pages:
            hierarchy_valid = False
        if node.get("number") and node["number"] not in node.get("text", ""):
            numbers_present = False
        if node["type"] == "table" and any(child.get("type") in {"clause", "subclause"} for child in node.get("children", [])):
            tables_without_clause_children = False
        for child in node.get("children", []):
            if child.get("number") and node.get("number"):
                if not child["number"].startswith(node["number"] + "."):
                    hierarchy_valid = False
    checks = {
        "same_document_id": result.get("document_id") == source.get("document_id"),
        "same_page_count": len(result_pages) == len(source_pages),
        "page_numbers_preserved": [page.get("page_number") for page in result_pages] == [page.get("page_number") for page in source_pages],
        "cleaned_text_preserved": [page.get("cleaned_text") for page in result_pages] == [page.get("cleaned_text") for page in source_pages],
        "raw_text_preserved": [page.get("raw_text") for page in result_pages] == [page.get("raw_text") for page in source_pages],
        "every_page_represented": represented == valid_pages,
        "clause_numbers_present_in_text": numbers_present,
        "hierarchy_consistent": hierarchy_valid,
        "tables_have_no_clause_children": tables_without_clause_children,
        "metadata_conflict_preserved": result.get("metadata_conflict") == source.get("metadata_conflict"),
        "source_url_preserved": result.get("source_url") == source.get("source_url"),
        "source_sha256_preserved": result.get("sha256") == source.get("sha256"),
    }
    checks["passed"] = all(checks.values())
    return checks


def structure_document(source_path: Path, output_path: Path) -> tuple[dict, dict]:
    source_bytes = source_path.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    source = json.loads(source_bytes)
    structure, ambiguous, _ = build_hierarchy(source.get("pages") or [])
    result = {key: value for key, value in source.items() if key != "structure"}
    result.update(
        {
            "cleaned_input_file": str(source_path),
            "cleaned_input_sha256": source_hash,
            "structured_at": utc_now(),
            "structure_method": "deterministic_bis_rules_v1",
            "structure": structure,
            "ambiguous_detections": ambiguous,
        }
    )
    checks = validate_structure(source, result)
    if not checks["passed"]:
        failed = [name for name, passed in checks.items() if name != "passed" and not passed]
        raise ValueError(f"Structure validation failed for {source.get('document_id')}: {failed}")
    result["structure_quality_checks"] = checks
    if source_path.read_bytes() != source_bytes:
        raise RuntimeError(f"Cleaned input changed during structure detection: {source_path}")
    atomic_json(output_path, result)
    counts = Counter(node["type"] for node in _walk(structure))
    stats = {
        "document_id": source.get("document_id"),
        "product_id": source.get("product_id"),
        "input_file": str(source_path),
        "output_file": str(output_path),
        "pages_processed": len(source.get("pages") or []),
        "sections_detected": counts["section"],
        "clauses_detected": counts["clause"],
        "subclauses_detected": counts["subclause"],
        "annexes_detected": counts["annex"],
        "tables_detected": counts["table"],
        "front_matter_blocks": counts["front_matter"],
        "references_sections": counts["references"],
        "ambiguous_detections": len(ambiguous),
        "requires_review": bool(ambiguous) or source.get("verification_status") == "needs_review",
        "quality_checks_passed": True,
    }
    return result, stats


def run_structure(settings, product_id: str | None = None, all_documents: bool = False) -> dict:
    cleaned_dir = settings.data_dir / "processed" / "cleaned"
    candidates: list[tuple[Path, dict]] = []
    for path in sorted(cleaned_dir.glob("*.json")):
        if path.name == "cleaning_report.json":
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if product_id and data.get("product_id") != product_id:
            continue
        candidates.append((path, data))
    # Stage 2.4 defaults to the three documents currently proven through 2.3.
    # --all deliberately means all cleaned inputs, never uncleaned PDFs.
    selection = "all_cleaned" if all_documents else product_id or "stage_2_3_test_documents"
    output_dir = settings.data_dir / "processed" / "structured"
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {
        "generated_at": utc_now(),
        "selection": selection,
        "documents_processed": 0,
        "documents_failed": 0,
        "sections_detected": 0,
        "clauses_detected": 0,
        "subclauses_detected": 0,
        "annexes_detected": 0,
        "tables_detected": 0,
        "ambiguous_detections": 0,
        "pages_processed": 0,
        "documents_requiring_review": [],
        "documents": [],
    }
    for path, data in candidates:
        try:
            _, stats = structure_document(path, output_dir / path.name)
            report["documents_processed"] += 1
            for key in (
                "sections_detected", "clauses_detected", "subclauses_detected",
                "annexes_detected", "tables_detected", "ambiguous_detections", "pages_processed",
            ):
                report[key] += stats[key]
            if stats["requires_review"]:
                report["documents_requiring_review"].append(data.get("document_id"))
        except Exception as exc:
            report["documents_failed"] += 1
            stats = {
                "document_id": data.get("document_id"),
                "product_id": data.get("product_id"),
                "status": "failed",
                "error": f"{type(exc).__name__}: {exc}",
            }
            report["documents_requiring_review"].append(data.get("document_id"))
        report["documents"].append(stats)
    atomic_json(output_dir / "structure_report.json", report)
    return report
