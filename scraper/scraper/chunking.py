from __future__ import annotations

import hashlib
import json
import os
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from .utils import atomic_json, utc_now


TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)

# Characters that may legitimately end a token. BGE uses WordPiece, so an
# arbitrary token edge can land mid-word ("require" + "##ments"). Every
# boundary the splitter picks is snapped back to one of these.
_SAFE_BREAK_CHARS = " \t\n\r\f\v.,;:!?)]}\"'/\\|"

DEFAULT_EMBEDDING_MAX_TOKENS = 450


@dataclass(frozen=True)
class ChunkConfig:
    target_tokens: int = 700
    soft_max_tokens: int = 1000
    hard_max_tokens: int = 1200
    overlap_tokens: int = 75
    tokenizer: str = "regex_v1"
    # Stage 3.1A. Safe ceiling for the *whole* embedding input
    # (context_prefix + text, special tokens included). Only consulted by
    # model-backed tokenizers; regex_v1 keeps its historical behaviour.
    embedding_max_tokens: int = DEFAULT_EMBEDDING_MAX_TOKENS
    # Room reserved for the table header when a table is split, so every piece
    # still carries the column headings needed to interpret its rows.
    table_header_max_tokens: int = 60

    def validate(self) -> None:
        if not (0 < self.target_tokens <= self.soft_max_tokens <= self.hard_max_tokens):
            raise ValueError("token limits must satisfy 0 < target <= soft maximum <= hard maximum")
        if not (0 <= self.overlap_tokens < self.target_tokens):
            raise ValueError("overlap must be non-negative and smaller than target")
        if self.embedding_max_tokens < 1:
            raise ValueError("embedding_max_tokens must be at least 1")
        if self.table_header_max_tokens < 0:
            raise ValueError("table_header_max_tokens must be non-negative")


class RegexTokenizer:
    name = "regex_v1"

    @staticmethod
    def spans(text: str) -> list[tuple[int, int]]:
        return [match.span() for match in TOKEN_RE.finditer(text)]

    def count(self, text: str) -> int:
        return len(self.spans(text))

    def cutoff(self, text: str, maximum: int) -> int:
        spans = self.spans(text)
        if len(spans) <= maximum:
            return len(text)
        return spans[maximum - 1][1]

    def overlap_start(self, text: str, end: int, overlap: int) -> int:
        if overlap <= 0:
            return end
        spans = [span for span in self.spans(text[:end])]
        if len(spans) <= overlap:
            return 0
        return spans[-overlap][0]


def resolve_embedding_max_tokens(explicit: int | None = None) -> int:
    """Explicit argument, then $EMBEDDING_MAX_TOKENS, then the safe default."""
    if explicit is not None:
        return int(explicit)
    raw = os.environ.get("EMBEDDING_MAX_TOKENS")
    if raw and raw.strip().isdigit():
        value = int(raw)
        if value < 1:
            raise ValueError("EMBEDDING_MAX_TOKENS must be at least 1")
        return value
    return DEFAULT_EMBEDDING_MAX_TOKENS


_TOKENIZER_CACHE: dict = {}


def build_tokenizer(name: str, use_cache: bool = True):
    # regex_v1 stays available as a generic fallback, but for the production
    # pipeline the embedding model's own tokenizer is authoritative: it is the
    # component that will actually consume these chunks, and a count from any
    # other tokenizer is only an estimate of what the model will see.
    if name == "regex_v1":
        return RegexTokenizer()
    if name == "bge":
        from .config import resolve_embedding_spec

        spec = resolve_embedding_spec()
        # Loading the model costs seconds and hundreds of megabytes, and
        # chunk_document builds a tokenizer per document. One instance is
        # enough; the tokenizer is read-only apart from its span cache.
        cache_key = (name, spec.model_name, spec.revision)
        if use_cache and cache_key in _TOKENIZER_CACHE:
            return _TOKENIZER_CACHE[cache_key]
        tokenizer = BgeTokenizer(spec)
        _TOKENIZER_CACHE[cache_key] = tokenizer
        return tokenizer
    raise ValueError(f"Unsupported tokenizer {name!r}; available: regex_v1, bge")


class BgeTokenizer:
    """The embedding model's real WordPiece tokenizer, used for token accounting.

    ``count`` returns content tokens, matching :class:`RegexTokenizer`, so the
    splitter logic is shared. ``input_count`` returns what the model actually
    receives, special tokens included, and that is the number the safe limit is
    checked against.
    """

    # Tells the splitter to budget against the combined prefix + text input
    # rather than the body alone.
    counts_embedding_input = True

    def __init__(self, spec, name: str = "bge"):
        from .embeddings import SentenceTransformerEmbedder

        self.name = name
        self.spec = spec
        embedder = SentenceTransformerEmbedder(spec)
        self._tokenizer = embedder.tokenizer
        self._model_max_length = int(
            getattr(self._tokenizer, "model_max_length", None) or spec.context_window or 512
        )
        self._dimension = embedder.dimension
        self._parameter_count = embedder.parameter_count
        self._library_versions = dict(embedder.library_versions)
        self._cache: dict[str, tuple] = {}
        # Measured, not assumed: [CLS] + [SEP] for this model.
        self._special_token_count = self._measure_special_tokens()

    def _measure_special_tokens(self) -> int:
        probe = "tokenizer special token probe"
        with_specials = len(self._tokenizer(probe, add_special_tokens=True)["input_ids"])
        without = len(self._tokenizer(probe, add_special_tokens=False)["input_ids"])
        return with_specials - without

    @property
    def model_max_length(self) -> int:
        return self._model_max_length

    @property
    def special_token_count(self) -> int:
        return self._special_token_count

    @property
    def content_budget(self) -> int:
        """Positions available for content once special tokens are reserved."""
        return self._model_max_length - self._special_token_count

    @property
    def embedding_dimension(self) -> int:
        return self._dimension

    def describe(self) -> dict:
        return {
            "tokenizer": self.name,
            "tokenizer_class": type(self._tokenizer).__name__,
            "model": self.spec.model_name,
            "model_revision": self.spec.revision,
            "provider": self.spec.provider,
            "model_max_length": self._model_max_length,
            "special_tokens": list(self._tokenizer.all_special_tokens),
            "special_token_count": self._special_token_count,
            "usable_content_token_budget": self.content_budget,
            "embedding_dimension": self._dimension,
            "parameter_count": self._parameter_count,
            "library_versions": self._library_versions,
        }

    def _spans(self, text: str) -> tuple:
        """Character spans of content tokens; cached because splitting is O(n^2)."""
        cached = self._cache.get(text)
        if cached is None:
            offsets = self._tokenizer(
                text, add_special_tokens=False, return_offsets_mapping=True
            )["offset_mapping"]
            spans = tuple((int(start), int(end)) for start, end in offsets if end > start)
            if len(self._cache) > 4096:
                self._cache.clear()
            self._cache[text] = spans
            cached = spans
        return cached

    def count(self, text: str) -> int:
        if not text:
            return 0
        return len(self._spans(text))

    def input_count(self, text: str, prefix: str = "") -> int:
        """Exactly what the model receives for this chunk, special tokens included."""
        head = str(prefix or "").strip()
        body = str(text or "").strip()
        combined = f"{head}\n\n{body}" if head else body
        if not combined:
            return self._special_token_count
        return self.count(combined) + self._special_token_count

    def prefix_cost(self, prefix: str) -> int:
        """Input cost of the prefix and its separator, as the model sees them.

        Special tokens are included, so this is directly subtractable from the
        safe limit.
        """
        head = str(prefix or "").strip()
        if not head:
            return self._special_token_count
        return self.count(f"{head}\n\n") + self._special_token_count

    def body_budget(self, prefix: str, max_input_tokens: int) -> int:
        """Content tokens the body may use once the prefix and specials are paid for.

        ``prefix_cost`` already counts the special tokens, so a body filling this
        budget exactly still lands on ``max_input_tokens`` and not two over it.
        """
        return max(0, max_input_tokens - self.prefix_cost(prefix))

    def cutoff(self, text: str, maximum: int) -> int:
        """Largest character offset whose prefix fits within ``maximum`` tokens."""
        spans = self._spans(text)
        if len(spans) <= maximum:
            return len(text)
        if maximum < 1:
            return 0
        return _snap_to_break(text, spans[maximum - 1][1])

    def overlap_start(self, text: str, end: int, overlap: int) -> int:
        if overlap <= 0:
            return end
        spans = [span for span in self._spans(text[:end]) if span[0] < end]
        if len(spans) <= overlap:
            return 0
        return spans[-overlap][0]


def _snap_to_break(text: str, offset: int) -> int:
    """Move a token boundary back to the nearest safe break.

    WordPiece token edges fall mid-word ("require" + "##ments"), so an unsnapped
    boundary would corrupt the text. The raw offset is returned only when a
    single unbreakable run exceeds the whole budget, where refusing to split
    would be worse than an imperfect edge.
    """
    offset = max(0, min(offset, len(text)))
    stripped = text[:offset].rstrip()
    if not stripped:
        return 0
    if stripped[-1] in _SAFE_BREAK_CHARS:
        return len(stripped)
    position = len(stripped) - 1
    while position > 0 and not stripped[position].isspace():
        position -= 1
    if position <= 0:
        return offset
    return position + 1


def _candidate_boundaries(text: str) -> list[int]:
    """Split candidates in descending order of preference.

    Preference order is paragraph, then line (a list item or table row in this
    corpus), then sentence. Only these are considered, so a split never lands
    inside a sentence, a numbered requirement, a definition, or a table row.
    """
    boundaries = {len(text)}
    for match in re.finditer(r"\n\s*\n", text):
        boundaries.add(match.end())
    for match in re.finditer(r"\n", text):
        boundaries.add(match.end())
    for match in re.finditer(r"[.!?](?:[\"')\]]*)\s+", text):
        boundaries.add(match.end())
    return sorted(boundaries)


def split_text_for_embedding(text: str, tokenizer, config: ChunkConfig, prefix: str) -> list[dict]:
    """Split so that every ``prefix + text`` piece fits the model's input limit.

    This is the Stage 3.1A splitter. It differs from :func:`split_text` in one
    decisive way: the budget is spent on the *whole* embedding input, so the
    prefix's real cost is subtracted before the body is measured. A chunk that
    already fits is returned untouched, so unsplit clauses are not disturbed.
    """
    if not text.strip():
        return []
    limit = config.embedding_max_tokens
    body_budget = tokenizer.body_budget(prefix, limit)
    if body_budget < 1:
        # The prefix alone fills the window. Nothing can be salvaged honestly.
        return []
    if tokenizer.input_count(text, prefix) <= limit:
        return [{"start": 0, "end": len(text), "text": text, "overlap_tokens": 0}]

    boundaries = [boundary for boundary in _candidate_boundaries(text) if 0 < boundary < len(text)]
    pieces: list[dict] = []
    start = 0
    previous_end = 0
    while start < len(text):
        remaining = text[start:]
        if tokenizer.count(remaining) <= body_budget:
            end = len(text)
        else:
            within_budget = [
                boundary for boundary in boundaries
                if boundary > start and tokenizer.count(text[start:boundary]) <= body_budget
            ]
            end = within_budget[-1] if within_budget else 0
            if end <= start:
                end = start + tokenizer.cutoff(remaining, body_budget)
        if end <= start:
            raise RuntimeError("token splitter made no progress")
        piece = text[start:end]
        count = tokenizer.count(piece)
        # Belt and braces: the budget above is exact, but WordPiece is not
        # perfectly additive at every seam, so the real input length is
        # re-checked and the piece is re-cut to the true allowance if needed.
        while piece and tokenizer.input_count(piece, prefix) > limit:
            allowance = tokenizer.body_budget(prefix, limit)
            if tokenizer.count(piece) <= allowance:
                break
            trimmed = start + tokenizer.cutoff(piece, allowance)
            if trimmed <= start:
                trimmed = end - 1
            if trimmed <= start:
                break
            end = trimmed
            piece = text[start:end]
            count = tokenizer.count(piece)
        overlap_count = tokenizer.count(text[start:previous_end]) if previous_end > start else 0
        pieces.append(
            {"start": start, "end": end, "text": piece, "overlap_tokens": overlap_count}
        )
        if end >= len(text):
            break
        allowed_overlap = min(config.overlap_tokens, max(0, count // 4))
        next_start = tokenizer.overlap_start(text, end, allowed_overlap)
        if next_start <= start:
            next_start = end
        previous_end = end
        start = next_start
    return pieces


def split_text(text: str, tokenizer, config: ChunkConfig) -> list[dict]:
    """Split into exact, contiguous source substrings with small intra-unit overlap."""
    if not text.strip():
        return []
    if tokenizer.count(text) <= config.soft_max_tokens:
        return [{"start": 0, "end": len(text), "text": text, "overlap_tokens": 0}]
    boundaries = _candidate_boundaries(text)
    pieces: list[dict] = []
    start = 0
    previous_end = 0
    while start < len(text):
        remaining = text[start:]
        if tokenizer.count(remaining) <= config.soft_max_tokens:
            end = len(text)
        else:
            within_target = [
                boundary for boundary in boundaries
                if boundary > start and tokenizer.count(text[start:boundary]) <= config.target_tokens
            ]
            end = within_target[-1] if within_target else start
            if end == start:
                within_hard = [
                    boundary for boundary in boundaries
                    if boundary > start and tokenizer.count(text[start:boundary]) <= config.hard_max_tokens
                ]
                end = within_hard[-1] if within_hard else start + tokenizer.cutoff(remaining, config.hard_max_tokens)
        if end <= start:
            raise RuntimeError("token splitter made no progress")
        piece = text[start:end]
        count = tokenizer.count(piece)
        if count > config.hard_max_tokens:
            end = start + tokenizer.cutoff(piece, config.hard_max_tokens)
            piece = text[start:end]
            count = tokenizer.count(piece)
        overlap_count = tokenizer.count(text[start:previous_end]) if previous_end > start else 0
        pieces.append(
            {"start": start, "end": end, "text": piece, "overlap_tokens": overlap_count}
        )
        if end >= len(text):
            break
        allowed_overlap = min(config.overlap_tokens, max(0, count // 4))
        next_start = tokenizer.overlap_start(text, end, allowed_overlap)
        if next_start <= start:
            next_start = end
        previous_end = end
        start = next_start
    return pieces


def _page_ranges(node: dict) -> list[tuple[int, int, int]]:
    text = str(node.get("text") or "")
    cursor = 0
    ranges: list[tuple[int, int, int]] = []
    for segment in node.get("page_segments") or []:
        segment_text = str(segment.get("text") or "")
        if not segment_text:
            continue
        start = text.find(segment_text, cursor)
        if start < 0:
            start = text.find(segment_text)
        if start < 0:
            continue
        end = start + len(segment_text)
        ranges.append((start, end, segment["page_number"]))
        cursor = end
    return ranges


def _pages_for_piece(node: dict, start: int, end: int) -> list[int]:
    pages = [page for page_start, page_end, page in _page_ranges(node) if start < page_end and end > page_start]
    if pages:
        return list(dict.fromkeys(pages))
    return list(node.get("pages") or [])


def _walk_units(nodes: list[dict], ancestors: list[dict] | None = None):
    ancestors = ancestors or []
    for node in nodes:
        yield node, ancestors
        yield from _walk_units(node.get("children") or [], ancestors + [node])


def _ancestor(ancestors: list[dict], kinds: set[str]) -> dict | None:
    return next((node for node in reversed(ancestors) if node.get("type") in kinds), None)


def _context_prefix(document: dict, node: dict, ancestors: list[dict]) -> str:
    parts: list[str] = []
    standards = document.get("standard_numbers") or []
    if standards:
        parts.append(", ".join(standards))
    annex = node if node.get("type") == "annex" else _ancestor(ancestors, {"annex"})
    if annex:
        label = f"Annex {annex.get('identifier')}"
        if annex.get("title"):
            label += f" — {annex['title']}"
        parts.append(label)
    section = node if node.get("type") == "section" else _ancestor(ancestors, {"section"})
    if section:
        label = f"Section {section.get('number')}"
        if section.get("title"):
            label += f" — {section['title']}"
        parts.append(label)
    clause = node if node.get("type") in {"clause", "subclause"} else _ancestor(ancestors, {"clause", "subclause"})
    if clause:
        label = f"Clause {clause.get('number')}"
        if clause.get("title"):
            label += f" — {clause['title']}"
        parts.append(label)
    if node.get("type") == "table":
        label = f"Table {node.get('identifier')}"
        if node.get("title"):
            label += f" — {node['title']}"
        parts.append(label)
    return " | ".join(parts)


def _structural_path(node: dict, ancestors: list[dict]) -> list[dict]:
    result = []
    for item in ancestors + [node]:
        entry = {"type": item.get("type")}
        if item.get("number") is not None:
            entry["number"] = item["number"]
        if item.get("identifier") is not None:
            entry["identifier"] = item["identifier"]
        if item.get("title") is not None:
            entry["title"] = item["title"]
        result.append(entry)
    return result


def _unit_key(node: dict, ancestors: list[dict], ordinal: int) -> str:
    path = []
    for item in ancestors + [node]:
        identity = item.get("number") or item.get("identifier") or item.get("title") or "untitled"
        path.append(f"{item.get('type')}:{identity}")
    return "/".join(path) + f"/unit:{ordinal}"


def unit_signature(document_id: str, node: dict, ancestors: list[dict], text: str) -> str:
    """Identity of a structural unit that does not depend on its walk position.

    ``_unit_key`` mixes in an ordinal, which makes it unusable for checking the
    output against the structured input. This key is built from the structural
    path plus the unit's own text digest, so it stays stable no matter how the
    tree is walked, and two units that would produce identical chunks are
    interchangeable by construction.
    """
    material = json.dumps(
        {
            "document_id": document_id,
            "path": _structural_path(node, ancestors),
            "type": node.get("type"),
            "text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def _is_context_only_container(node: dict, text: str, tokenizer) -> bool:
    if not node.get("children"):
        return False
    if node.get("type") in {"annex", "section"}:
        return tokenizer.count(text) <= 30
    if node.get("type") in {"clause", "subclause"} and node.get("number"):
        normalize = lambda value: re.sub(r"[^\w]+", " ", str(value).casefold()).strip()
        expected = normalize(f"{node['number']} {node.get('title') or ''}")
        return normalize(text) == expected
    return False


def _table_header_context(text: str, tokenizer, maximum_tokens: int = 200, max_lines: int = 20) -> str:
    lines: list[str] = []
    for line in text.splitlines():
        candidate = "\n".join(lines + [line]).strip()
        if lines and tokenizer.count(candidate) > maximum_tokens:
            break
        lines.append(line)
        if len(lines) >= max_lines:
            break
    return "\n".join(lines).strip()


def _chunk_id(document_id: str, unit_key: str, chunk_index: int) -> str:
    material = f"{document_id}\n{unit_key}\n{chunk_index}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def _unit_chunk_id(document_id: str, unit_key: str, chunk_index: int, text: str) -> str:
    """Deterministic id for a Stage 3.1A piece.

    The piece text is hashed in so an id can never be reused for different
    content. A unit that had to be split gets fresh ids; a unit that did not is
    handed back the Stage 2.5 id verbatim, which is why those ids stay stable.
    """
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    material = f"{document_id}\n{unit_key}\n{chunk_index}\n{digest}".encode("utf-8")
    return hashlib.sha256(material).hexdigest()[:24]


def chunk_document(document: dict, config: ChunkConfig) -> tuple[list[dict], dict]:
    """Clause-aware chunking. ``config.tokenizer`` decides how tokens are counted.

    With ``regex_v1`` this is the Stage 2.5 behaviour, unchanged. With ``bge``
    the embedding model's own tokenizer is authoritative and every piece is
    sized against the whole ``context_prefix + text`` input, so nothing is
    truncated at embedding time.
    """
    config.validate()
    tokenizer = build_tokenizer(config.tokenizer)
    # Imported lazily: embeddings pulls in torch, and Stage 2.5 consumers of
    # this module must not need the model stack installed.
    from .embeddings import embedding_prefix

    embedding_aware = bool(getattr(tokenizer, "counts_embedding_input", False))
    chunks: list[dict] = []
    unit_ordinal = 0
    split_units = 0
    split_clauses = 0
    tables_chunked = 0
    annex_chunks = 0
    header_limit = config.table_header_max_tokens if embedding_aware else 200

    for node, ancestors in _walk_units(document.get("structure") or []):
        text = str(node.get("text") or "")
        if not text.strip():
            continue
        annex = node if node.get("type") == "annex" else _ancestor(ancestors, {"annex"})
        section = node if node.get("type") == "section" else _ancestor(ancestors, {"section"})
        clause = node if node.get("type") in {"clause", "subclause"} else _ancestor(ancestors, {"clause", "subclause"})
        parent_clause = _ancestor(ancestors, {"clause", "subclause"})
        prefix = _context_prefix(document, node, ancestors)
        is_table = node.get("type") == "table"
        table_header_context = _table_header_context(text, tokenizer, header_limit) if is_table else None
        # What the embedding step will actually prepend. Budgeting against this
        # rather than the bare context_prefix is what guarantees no truncation.
        embed_prefix = embedding_prefix(
            {"context_prefix": prefix, "table_header_context": table_header_context}
        )
        if embedding_aware:
            pieces = split_text_for_embedding(text, tokenizer, config, embed_prefix)
        else:
            pieces = split_text(text, tokenizer, config)
        if not pieces:
            continue
        if len(pieces) > 1:
            split_units += 1
            if node.get("type") in {"clause", "subclause"}:
                split_clauses += 1
        if is_table:
            tables_chunked += 1
        context_only = _is_context_only_container(node, text, tokenizer)
        unit_key = _unit_key(node, ancestors, unit_ordinal)
        unit_ordinal += 1
        signature = unit_signature(str(document.get("document_id")), node, ancestors, text)
        # Under regex_v1 the identifier scheme is untouched, so the Stage 2.5
        # corpus is reproduced exactly. Under bge, an unsplit unit keeps its
        # Stage 2.5 id and a re-split unit gets content-derived ids, so an id is
        # never reused for different text.
        use_legacy_ids = not embedding_aware
        was_split = len(pieces) > 1
        for chunk_index, piece in enumerate(pieces):
            pages = _pages_for_piece(node, piece["start"], piece["end"])
            if use_legacy_ids or not was_split:
                chunk_id = _chunk_id(str(document.get("document_id")), unit_key, chunk_index)
            else:
                chunk_id = _unit_chunk_id(
                    str(document.get("document_id")), unit_key, chunk_index, piece["text"]
                )
            record = {
                "chunk_id": chunk_id,
                "document_id": document.get("document_id"),
                "standard_numbers": document.get("standard_numbers") or [],
                "document_title": document.get("title"),
                "document_type": document.get("document_type"),
                "organization": document.get("organization"),
                "structural_type": node.get("type"),
                "structural_path": _structural_path(node, ancestors),
                "section_number": section.get("number") if section else None,
                "section_title": section.get("title") if section else None,
                "clause_number": clause.get("number") if clause else None,
                "clause_title": clause.get("title") if clause else None,
                "parent_clause_number": parent_clause.get("number") if parent_clause else None,
                "annex_identifier": annex.get("identifier") if annex else None,
                "annex_title": annex.get("title") if annex else None,
                "table_identifier": node.get("identifier") if node.get("type") == "table" else None,
                "table_title": node.get("title") if node.get("type") == "table" else None,
                "table_header_context": table_header_context,
                "pages": pages,
                "start_page": min(pages) if pages else None,
                "end_page": max(pages) if pages else None,
                "chunk_index": chunk_index,
                "chunk_count": len(pieces),
                "context_prefix": prefix,
                "text": piece["text"],
                # Character offsets of this piece inside its structural unit's
                # text. Makes "no source content was dropped" checkable rather
                # than assumed, and pins each chunk to an exact source range.
                "unit_text_start": piece["start"],
                "unit_text_end": piece["end"],
                "unit_text_length": len(text),
                "unit_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                "_unit_key": unit_key,
                "_unit_signature": signature,
                "token_count": tokenizer.count(piece["text"]),
                "context_token_count": tokenizer.count(prefix),
                "retrieval_token_count": tokenizer.count(prefix) + tokenizer.count(piece["text"]),
                "overlap_tokens": piece["overlap_tokens"],
                "tokenizer": tokenizer.name,
                "source_url": document.get("source_url"),
                "source_file": document.get("source_file") or document.get("local_file"),
                "sha256": document.get("sha256"),
                "structured_input_file": document.get("_structured_input_file"),
                "structured_input_sha256": document.get("_structured_input_sha256"),
                "metadata_conflict": document.get("metadata_conflict"),
                "conflict_type": document.get("conflict_type"),
                "verification_status": document.get("verification_status"),
                "_context_only_container": context_only,
            }
            if embedding_aware:
                # The number that actually decides whether the model truncates.
                record["embedding_input_tokens"] = tokenizer.input_count(piece["text"], embed_prefix)
                record["embedding_max_tokens"] = config.embedding_max_tokens
            chunk = {key: value for key, value in record.items() if value is not None}
            chunks.append(chunk)

    # Parent headings are useful context but poor standalone evidence. Remove
    # them only when every page they reference is already represented by a
    # substantive child/peer chunk; otherwise retain them to preserve coverage.
    filtered: list[dict] = []
    for chunk in chunks:
        other_pages = {
            page
            for other in chunks
            if other is not chunk and not other.get("_context_only_container")
            for page in other.get("pages") or []
        }
        if chunk.get("_context_only_container") and set(chunk.get("pages") or []) <= other_pages:
            continue
        filtered.append(chunk)
    chunks = filtered
    for chunk in chunks:
        chunk.pop("_context_only_container", None)
    annex_chunks = sum(bool(chunk.get("annex_identifier")) for chunk in chunks)

    stats = {
        "document_id": document.get("document_id"),
        "standard_numbers": document.get("standard_numbers") or [],
        "pages_processed": len(document.get("pages") or []),
        "chunks_created": len(chunks),
        "structural_units_split": split_units,
        "clauses_split": split_clauses,
        "tables_chunked": tables_chunked,
        "annex_chunks": annex_chunks,
    }
    return chunks, stats


def _missing_metadata(chunk: dict) -> list[str]:
    required = ("chunk_id", "document_id", "pages", "start_page", "end_page", "text", "source_url", "source_file", "sha256")
    missing = []
    for field in required:
        value = chunk.get(field)
        if value is None or value == "" or value == () or value == []:
            missing.append(field)
    return missing


def validate_chunks(document: dict, chunks: list[dict], config: ChunkConfig) -> dict:
    tokenizer = build_tokenizer(config.tokenizer)
    page_numbers = {page["page_number"] for page in document.get("pages") or []}
    represented_pages = {page for chunk in chunks for page in chunk.get("pages") or []}
    ids = [chunk["chunk_id"] for chunk in chunks]
    text_faithful = True
    node_texts = [node.get("text", "") for node, _ in _walk_units(document.get("structure") or [])]
    for chunk in chunks:
        if not any(chunk["text"] in source for source in node_texts):
            text_faithful = False
            break
    repeat, _ = chunk_document(document, config)
    checks = {
        "all_chunks_have_document_id": all(chunk.get("document_id") for chunk in chunks),
        "all_chunks_have_provenance": all(not _missing_metadata(chunk) for chunk in chunks),
        "all_chunks_have_pages": all(chunk.get("pages") for chunk in chunks),
        "page_provenance_valid": all(set(chunk["pages"]) <= page_numbers for chunk in chunks),
        "all_pages_represented": represented_pages == page_numbers,
        "source_text_not_rewritten": text_faithful,
        "hard_max_respected": all(tokenizer.count(chunk["text"]) <= config.hard_max_tokens for chunk in chunks),
        "chunk_ids_unique": len(ids) == len(set(ids)),
        "chunk_ids_deterministic": ids == [chunk["chunk_id"] for chunk in repeat],
        "metadata_conflict_preserved": all(chunk.get("metadata_conflict") == document.get("metadata_conflict") for chunk in chunks),
        "tables_preserved": all(
            any(chunk.get("structural_type") == "table" and chunk.get("table_identifier") == node.get("identifier") for chunk in chunks)
            for node, _ in _walk_units(document.get("structure") or []) if node.get("type") == "table"
        ),
    }
    if getattr(tokenizer, "counts_embedding_input", False):
        from .embeddings import embedding_prefix

        counts = [
            tokenizer.input_count(chunk["text"], embedding_prefix(chunk)) for chunk in chunks
        ]
        checks["embedding_input_within_safe_limit"] = all(
            count <= config.embedding_max_tokens for count in counts
        )
        checks["recorded_embedding_tokens_match"] = all(
            chunk.get("embedding_input_tokens") == count for chunk, count in zip(chunks, counts)
        )
        checks["no_embedding_input_truncated"] = all(
            count <= tokenizer.model_max_length for count in counts
        )
    checks["passed"] = all(checks.values())
    return checks


def run_chunking(settings, product_id: str | None = None, all_documents: bool = False,
                 config: ChunkConfig | None = None, output_dir: Path | None = None,
                 selection_label: str | None = None) -> dict:
    config = config or ChunkConfig()
    config.validate()
    tokenizer = build_tokenizer(config.tokenizer)
    structured_dir = settings.data_dir / "processed" / "structured"
    documents: list[tuple[Path, dict]] = []
    for path in sorted(structured_dir.glob("*.json")):
        if path.name == "structure_report.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if product_id and document.get("product_id") != product_id:
            continue
        document["_structured_input_file"] = str(path)
        document["_structured_input_sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
        documents.append((path, document))

    output_dir = output_dir or (settings.data_dir / "processed" / "chunks")
    output_dir.mkdir(parents=True, exist_ok=True)
    all_chunks: list[dict] = []
    document_stats: list[dict] = []
    for path, document in documents:
        before = path.read_bytes()
        chunks, stats = chunk_document(document, config)
        checks = validate_chunks(document, chunks, config)
        if not checks["passed"]:
            failed = [name for name, passed in checks.items() if name != "passed" and not passed]
            raise ValueError(f"Chunk validation failed for {document.get('document_id')}: {failed}")
        if path.read_bytes() != before:
            raise RuntimeError(f"Structured input changed during chunking: {path}")
        stats["quality_checks"] = checks
        document_stats.append(stats)
        all_chunks.extend(chunks)

    jsonl_path = output_dir / "chunks.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n" for chunk in all_chunks),
        encoding="utf-8",
    )
    token_counts = [chunk["token_count"] for chunk in all_chunks]
    standards: Counter = Counter()
    per_document: Counter = Counter()
    for chunk in all_chunks:
        per_document[chunk["document_id"]] += 1
        for standard in chunk.get("standard_numbers") or []:
            standards[standard] += 1
    missing = [
        {"chunk_id": chunk["chunk_id"], "missing_fields": _missing_metadata(chunk)}
        for chunk in all_chunks if _missing_metadata(chunk)
    ]
    report = {
        "generated_at": utc_now(),
        "selection": selection_label or ("all_structured" if all_documents else product_id or "stage_2_4_test_documents"),
        "tokenizer": tokenizer.name,
        "tokenizer_detail": tokenizer.describe() if getattr(tokenizer, "counts_embedding_input", False) else None,
        "limits": {
            "target_tokens": config.target_tokens,
            "soft_max_tokens": config.soft_max_tokens,
            "hard_max_tokens": config.hard_max_tokens,
            "overlap_tokens": config.overlap_tokens,
            "embedding_max_tokens": config.embedding_max_tokens,
            "table_header_max_tokens": config.table_header_max_tokens,
        },
        "documents_processed": len(documents),
        "pages_processed": sum(stats["pages_processed"] for stats in document_stats),
        "chunks_created": len(all_chunks),
        "chunks_per_document": dict(sorted(per_document.items())),
        "chunks_per_standard": dict(sorted(standards.items())),
        "average_token_count": round(sum(token_counts) / len(token_counts), 2) if token_counts else 0,
        "minimum_token_count": min(token_counts) if token_counts else 0,
        "maximum_token_count": max(token_counts) if token_counts else 0,
        "chunks_below_target": sum(count < config.target_tokens for count in token_counts),
        "chunks_above_soft_maximum": sum(count > config.soft_max_tokens for count in token_counts),
        "chunks_above_hard_maximum": sum(count > config.hard_max_tokens for count in token_counts),
        "structural_units_requiring_splitting": sum(stats["structural_units_split"] for stats in document_stats),
        "clauses_split": sum(stats["clauses_split"] for stats in document_stats),
        "tables_chunked": sum(stats["tables_chunked"] for stats in document_stats),
        "annex_chunks": sum(stats["annex_chunks"] for stats in document_stats),
        "chunks_with_missing_metadata": len(missing),
        "metadata_errors": missing,
        "documents": document_stats,
    }
    if getattr(tokenizer, "counts_embedding_input", False):
        from .embeddings import embedding_prefix

        input_counts = [
            tokenizer.input_count(chunk["text"], embedding_prefix(chunk)) for chunk in all_chunks
        ]
        report["embedding_inputs"] = {
            "max_input_tokens": max(input_counts) if input_counts else 0,
            "mean_input_tokens": round(sum(input_counts) / len(input_counts), 2) if input_counts else 0,
            "safe_limit": config.embedding_max_tokens,
            "model_max_length": tokenizer.model_max_length,
            "chunks_over_safe_limit": sum(count > config.embedding_max_tokens for count in input_counts),
            "chunks_that_would_truncate": sum(count > tokenizer.model_max_length for count in input_counts),
        }
    atomic_json(output_dir / "chunking_report.json", report)
    return report
