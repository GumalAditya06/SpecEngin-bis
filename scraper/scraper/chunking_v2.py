"""Stage 3.1A: realign chunk boundaries to the embedding tokenizer.

Stage 3.1 found that 10 of the 83 Stage 2.5 chunks exceeded the BGE model's
512-token input window and were silently truncated at embedding time. This
module re-runs clause-aware chunking with the embedding model's own tokenizer
as the authoritative counter and writes a *new* corpus alongside the old one so
the two can be compared.

Nothing upstream is touched. PDFs, extraction, cleaning, structure detection,
and the Stage 2.5 ``chunks.jsonl`` are all read-only here; only the retrieval
chunk boundaries change.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Sequence

from .chunking import (
    ChunkConfig,
    build_tokenizer,
    chunk_document,
    resolve_embedding_max_tokens,
)
from .config import Settings
from .embeddings import embedding_prefix
from .utils import atomic_json, utc_now

# The Stage 2.5 limits, replayed so the old corpus can be reproduced in memory
# and compared chunk-for-chunk without re-running the old pipeline on disk.
LEGACY_LIMITS = {"target_tokens": 700, "soft_max_tokens": 1000, "hard_max_tokens": 1200, "overlap_tokens": 75}


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _unit_groups(chunks: Sequence[dict]) -> dict[str, list[dict]]:
    """Group chunks by the structural unit they came from."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for chunk in chunks:
        groups[chunk["_unit_signature"]].append(chunk)
    for pieces in groups.values():
        pieces.sort(key=lambda chunk: chunk["chunk_index"])
    return groups


def _piece_offsets(piece: dict) -> tuple[int, int]:
    """Character range of a piece, accepting splitter output or a chunk record."""
    if "unit_text_start" in piece:
        return int(piece["unit_text_start"]), int(piece["unit_text_end"])
    return int(piece["start"]), int(piece["end"])


def _merge_intervals(pieces: Sequence[dict]) -> list[tuple[int, int]]:
    intervals = sorted(_piece_offsets(piece) for piece in pieces)
    merged: list[tuple[int, int]] = []
    for start, end in intervals:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def unit_source_coverage(unit_length: int, pieces: Sequence[dict]) -> bool:
    """True when the pieces' character ranges tile the unit's text exactly.

    This is the no-information-loss check. It compares offsets rather than
    comparing strings, so duplicated, reordered, or rewritten text cannot mask a
    gap: any character the splitter dropped leaves an uncovered interval. It
    accepts either raw ``split_text_for_embedding`` output or chunk records.
    """
    if not pieces:
        return unit_length == 0
    merged = _merge_intervals(pieces)
    return len(merged) == 1 and merged[0] == (0, unit_length)


def _structured_units(documents: Sequence[dict]) -> dict[str, str]:
    """Map unit signature -> the node's own text, read straight from the input.

    Rebuilt here with the same signature function the chunker used, so the
    coverage check is anchored to the structured document rather than to the
    chunker's own bookkeeping.
    """
    from .chunking import _walk_units, unit_signature

    units: dict[str, str] = {}
    for document in documents:
        document_id = str(document.get("document_id"))
        for node, ancestors in _walk_units(document.get("structure") or []):
            text = str(node.get("text") or "")
            if not text.strip():
                continue
            units[unit_signature(document_id, node, ancestors, text)] = text
    return units


# ---------------------------------------------------------------------------
# Step 4: re-evaluate the existing corpus
# ---------------------------------------------------------------------------


def audit_chunks(chunks: Sequence[dict], tokenizer, config: ChunkConfig) -> dict:
    """Count every existing chunk the way the embedding model will see it."""
    rows: list[dict] = []
    for chunk in chunks:
        prefix = embedding_prefix(chunk)
        content = tokenizer.count(chunk["text"])
        prefix_cost = tokenizer.count(prefix) if prefix else 0
        input_tokens = tokenizer.input_count(chunk["text"], prefix)
        rows.append(
            {
                "chunk_id": chunk["chunk_id"],
                "document_id": chunk.get("document_id"),
                "standard_numbers": chunk.get("standard_numbers"),
                "structural_type": chunk.get("structural_type"),
                "clause_number": chunk.get("clause_number"),
                "annex_identifier": chunk.get("annex_identifier"),
                "table_identifier": chunk.get("table_identifier"),
                "pages": chunk.get("pages"),
                "regex_token_count": chunk.get("token_count"),
                "bge_token_count": content,
                "context_prefix_token_count": prefix_cost,
                "embedding_input_tokens": input_tokens,
                "model_max_length": tokenizer.model_max_length,
                "safe_limit": config.embedding_max_tokens,
                "exceeds_safe_limit": input_tokens > config.embedding_max_tokens,
                "would_truncate": input_tokens > tokenizer.model_max_length,
                "action_required": "split" if input_tokens > config.embedding_max_tokens else "keep_unchanged",
            }
        )
    counts = [row["embedding_input_tokens"] for row in rows]
    regex_counts = [int(row["regex_token_count"] or 0) for row in rows]
    ratios = [
        row["bge_token_count"] / row["regex_token_count"]
        for row in rows
        if row["regex_token_count"]
    ]
    return {
        "generated_at": utc_now(),
        "safe_limit": config.embedding_max_tokens,
        "model_max_length": tokenizer.model_max_length,
        "chunks_audited": len(rows),
        "chunks_exceeding_safe_limit": sum(row["exceeds_safe_limit"] for row in rows),
        "chunks_that_would_truncate": sum(row["would_truncate"] for row in rows),
        "max_embedding_input_tokens": max(counts) if counts else 0,
        "max_bge_token_count": max((row["bge_token_count"] for row in rows), default=0),
        "max_regex_token_count": max(regex_counts, default=0),
        "mean_bge_over_regex_ratio": round(sum(ratios) / len(ratios), 4) if ratios else 0,
        "understatement_factor": round(
            (sum(row["bge_token_count"] for row in rows) / sum(regex_counts)), 4
        ) if regex_counts else 0,
        "by_structural_type": dict(Counter(
            row["structural_type"] for row in rows if row["exceeds_safe_limit"]
        )),
        "chunks": rows,
    }


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

PROVENANCE_KEYS = (
    "document_id", "standard_numbers", "document_title", "document_type",
    "organization", "section_number", "section_title", "clause_number",
    "clause_title", "parent_clause_number", "annex_identifier", "annex_title",
    "table_identifier", "table_title", "pages", "start_page", "end_page",
    "source_url", "source_file", "sha256", "context_prefix",
)


def validate_v2(new_chunks: Sequence[dict], old_chunks: Sequence[dict],
                documents: Sequence[dict], tokenizer, config: ChunkConfig) -> dict:
    old_by_id = {chunk["chunk_id"]: chunk for chunk in old_chunks}
    unit_texts = _structured_units(documents)
    groups = _unit_groups(new_chunks)
    input_counts = [tokenizer.input_count(chunk["text"], embedding_prefix(chunk)) for chunk in new_chunks]
    ids = [chunk["chunk_id"] for chunk in new_chunks]

    # Only chunks that kept their Stage 2.5 identifier can be compared field by
    # field; a re-split piece is a new chunk and inherits metadata, not identity.
    provenance_errors: list[dict] = []
    for chunk in new_chunks:
        previous = old_by_id.get(chunk["chunk_id"])
        if previous is None:
            continue
        mismatched = [key for key in PROVENANCE_KEYS if chunk.get(key) != previous.get(key)]
        if mismatched:
            provenance_errors.append({"chunk_id": chunk["chunk_id"], "mismatched": mismatched})
    coverage_failures = [
        key for key, pieces in groups.items()
        if not unit_source_coverage(pieces[0].get("unit_text_length", 0), pieces)
    ]
    unknown_units = sorted(key for key in groups if key not in unit_texts)
    # Split pieces of one unit must agree on the identity of their parent.
    split_metadata_errors = [
        key for key, pieces in groups.items() if len(pieces) > 1 and len({
            (
                piece.get("clause_number"), piece.get("section_number"),
                piece.get("annex_identifier"), piece.get("table_identifier"),
                piece.get("document_id"), piece.get("source_url"),
            ) for piece in pieces
        }) > 1
    ]
    index_errors = [
        key for key, pieces in groups.items()
        if [piece["chunk_index"] for piece in pieces] != list(range(len(pieces)))
        or any(piece.get("chunk_count") != len(pieces) for piece in pieces)
    ]
    table_pieces = [chunk for chunk in new_chunks if chunk.get("structural_type") == "table"]

    checks = {
        # 1 + 2: no chunk may exceed the safe limit, so nothing can be truncated.
        "all_embedding_inputs_within_safe_limit": all(
            count <= config.embedding_max_tokens for count in input_counts
        ),
        "no_truncation_possible": all(count <= tokenizer.model_max_length for count in input_counts),
        "recorded_token_counts_accurate": all(
            chunk.get("embedding_input_tokens") == count for chunk, count in zip(new_chunks, input_counts)
        ),
        # 4: every piece is an exact substring of its unit's source text, and
        # that text is the node's own text in the structured input.
        "source_text_preserved": not unknown_units and all(
            unit_texts[chunk["_unit_signature"]][
                int(chunk["unit_text_start"]):int(chunk["unit_text_end"])
            ] == chunk["text"]
            for chunk in new_chunks
            if chunk["_unit_signature"] in unit_texts
        ),
        "no_source_content_dropped": not coverage_failures,
        # 5 - 10: identity and provenance survive the boundary change.
        "clause_metadata_preserved": not split_metadata_errors,
        "page_metadata_preserved": all(chunk.get("pages") for chunk in new_chunks),
        "annex_metadata_preserved": all(
            chunk.get("annex_identifier") is not None
            for chunk in new_chunks
            if (chunk.get("structural_path") or [{}])[-1].get("type") == "annex"
        ),
        "table_metadata_preserved": all(chunk.get("table_identifier") for chunk in table_pieces),
        "table_headers_available": all(chunk.get("table_header_context") for chunk in table_pieces),
        "source_url_preserved": all(chunk.get("source_url") for chunk in new_chunks),
        "document_id_preserved": all(chunk.get("document_id") for chunk in new_chunks),
        "unchanged_chunks_keep_provenance": not provenance_errors,
        "chunk_index_and_count_consistent": not index_errors,
        # 11: identifiers are unique and deterministic (determinism is asserted
        # by the runner, which re-chunks and compares).
        "chunk_ids_unique": len(ids) == len(set(ids)),
    }
    checks["passed"] = all(checks.values())
    return {
        "checks": checks,
        "details": {
            "chunks": len(new_chunks),
            "max_embedding_input_tokens": max(input_counts) if input_counts else 0,
            "safe_limit": config.embedding_max_tokens,
            "over_limit_chunk_ids": [
                chunk["chunk_id"] for chunk, count in zip(new_chunks, input_counts)
                if count > config.embedding_max_tokens
            ],
            "coverage_failure_units": coverage_failures,
            "units_not_found_in_structured_input": unknown_units,
            "split_metadata_error_units": split_metadata_errors,
            "chunk_index_error_units": index_errors,
            "provenance_errors": provenance_errors[:20],
            "table_chunk_count": len(table_pieces),
        },
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def run_chunking_v2(settings: Settings, config: ChunkConfig | None = None,
                    product_id: str | None = None, all_documents: bool = False) -> dict:
    config = config or ChunkConfig(tokenizer="bge", embedding_max_tokens=resolve_embedding_max_tokens())
    config.validate()
    if config.tokenizer != "bge":
        raise ValueError("Stage 3.1A requires the BGE tokenizer; pass tokenizer='bge'")
    tokenizer = build_tokenizer("bge")

    processed = settings.data_dir / "processed"
    old_chunks_path = processed / "chunks" / "chunks.jsonl"
    if not old_chunks_path.exists():
        raise FileNotFoundError(f"{old_chunks_path} not found. Run `scraper chunk` first.")
    old_chunks = _read_jsonl(old_chunks_path)
    old_bytes = old_chunks_path.read_bytes()

    structured_dir = processed / "structured"
    documents: list[tuple[Path, dict]] = []
    for path in sorted(structured_dir.glob("*.json")):
        if path.name == "structure_report.json":
            continue
        document = json.loads(path.read_text(encoding="utf-8"))
        if product_id and document.get("product_id") != product_id:
            continue
        document["_structured_input_file"] = str(path)
        document["_structured_input_sha256"] = _sha256(path)
        documents.append((path, document))

    # Audit the existing corpus before changing anything (Step 4).
    audit = audit_chunks(old_chunks, tokenizer, config)

    legacy_config = ChunkConfig(tokenizer="regex_v1", **LEGACY_LIMITS)
    new_chunks: list[dict] = []
    legacy_chunks: list[dict] = []
    document_stats: list[dict] = []
    for path, document in documents:
        before = path.read_bytes()
        legacy, _ = chunk_document(document, legacy_config)
        chunks, stats = chunk_document(document, config)
        checks = validate_chunks_or_raise(document, chunks, config)
        if path.read_bytes() != before:
            raise RuntimeError(f"Structured input changed during chunking: {path}")
        stats["quality_checks"] = checks
        document_stats.append(stats)
        new_chunks.extend(chunks)
        legacy_chunks.extend(legacy)

    # Determinism: the same inputs must produce the same identifiers.
    repeat: list[dict] = []
    for _, document in documents:
        repeat.extend(chunk_document(document, config)[0])
    deterministic = [c["chunk_id"] for c in repeat] == [c["chunk_id"] for c in new_chunks]

    validation = validate_v2(new_chunks, old_chunks, [d for _, d in documents], tokenizer, config)
    validation["checks"]["chunk_ids_deterministic"] = deterministic
    validation["checks"]["passed"] = all(
        v for k, v in validation["checks"].items() if k != "passed"
    )
    if not validation["checks"]["passed"]:
        failed = [k for k, v in validation["checks"].items() if k != "passed" and not v]
        raise ValueError(f"Stage 3.1A validation failed: {failed}")

    # Upstream inputs must be untouched.
    upstream = _verify_upstream_unchanged(processed)

    output_dir = processed / "chunks_v2"
    output_dir.mkdir(parents=True, exist_ok=True)
    comparison = _compare(old_chunks, new_chunks, legacy_chunks, audit, tokenizer, config)

    for chunk in new_chunks:
        chunk.pop("_unit_key", None)
    jsonl_path = output_dir / "chunks.jsonl"
    jsonl_path.write_text(
        "".join(json.dumps(chunk, ensure_ascii=False, sort_keys=True) + "\n" for chunk in new_chunks),
        encoding="utf-8",
    )

    input_counts = [tokenizer.input_count(c["text"], embedding_prefix(c)) for c in new_chunks]
    report = {
        "generated_at": utc_now(),
        "stage": "3.1A",
        "selection": "all_structured" if all_documents else product_id or "stage_3_1_chunks",
        "tokenizer": tokenizer.name,
        "tokenizer_detail": tokenizer.describe(),
        "limits": {
            "embedding_max_tokens": config.embedding_max_tokens,
            "overlap_tokens": config.overlap_tokens,
            "table_header_max_tokens": config.table_header_max_tokens,
        },
        "documents_processed": len(documents),
        "pages_processed": sum(stats["pages_processed"] for stats in document_stats),
        "chunks_created": len(new_chunks),
        "chunks_per_document": dict(sorted(Counter(c["document_id"] for c in new_chunks).items())),
        "chunks_per_standard": dict(sorted(Counter(
            standard for c in new_chunks for standard in c.get("standard_numbers") or []
        ).items())),
        "max_embedding_input_tokens": max(input_counts) if input_counts else 0,
        "mean_embedding_input_tokens": round(sum(input_counts) / len(input_counts), 2) if input_counts else 0,
        "chunks_over_safe_limit": sum(count > config.embedding_max_tokens for count in input_counts),
        "chunks_that_would_truncate": sum(count > tokenizer.model_max_length for count in input_counts),
        "structural_units_requiring_splitting": sum(stats["structural_units_split"] for stats in document_stats),
        "clauses_split": sum(stats["clauses_split"] for stats in document_stats),
        "tables_chunked": sum(stats["tables_chunked"] for stats in document_stats),
        "annex_chunks": sum(stats["annex_chunks"] for stats in document_stats),
        "validation": {"checks": validation["checks"], "details": validation["details"]},
        "upstream_inputs_unchanged": upstream,
        "documents": document_stats,
    }
    atomic_json(output_dir / "chunking_report.json", report)
    atomic_json(output_dir / "comparison_report.json", comparison)
    if old_chunks_path.read_bytes() != old_bytes:
        raise RuntimeError(f"Stage 2.5 corpus changed during Stage 3.1A: {old_chunks_path}")
    return {"report": report, "comparison": comparison, "audit": audit,
            "chunks": new_chunks, "tokenizer": tokenizer, "config": config}


def validate_chunks_or_raise(document: dict, chunks: list[dict], config: ChunkConfig) -> dict:
    from .chunking import validate_chunks

    checks = validate_chunks(document, chunks, config)
    if not checks["passed"]:
        failed = [name for name, passed in checks.items() if name != "passed" and not passed]
        raise ValueError(f"Chunk validation failed for {document.get('document_id')}: {failed}")
    return checks


def _verify_upstream_unchanged(processed: Path) -> dict:
    """Hash the upstream stages so the report can show they were not modified."""
    result: dict = {}
    for name in ("raw", "metadata", "extracted", "cleaned", "structured", "chunks"):
        directory = processed.parent / name if name in {"raw"} else processed / name
        if not directory.exists():
            continue
        files = sorted(p for p in directory.rglob("*") if p.is_file())
        combined = hashlib.sha256()
        for path in files:
            combined.update(str(path.relative_to(processed.parent)).encode("utf-8"))
            combined.update(_sha256(path).encode("utf-8"))
        result[name] = {"files": len(files), "tree_sha256": combined.hexdigest()}
    return result


def _compare(old_chunks: Sequence[dict], new_chunks: Sequence[dict],
             legacy_replay: Sequence[dict], audit: dict, tokenizer, config: ChunkConfig) -> dict:
    """Chunk-level accounting of what changed between Stage 2.5 and 3.1A."""
    old_ids = {chunk["chunk_id"] for chunk in old_chunks}
    new_ids = {chunk["chunk_id"] for chunk in new_chunks}
    kept = old_ids & new_ids

    legacy_groups = _unit_groups(legacy_replay)
    new_groups = _unit_groups(new_chunks)
    split_units = [
        {
            "unit_key": key,
            "clause_number": pieces[0].get("clause_number"),
            "annex_identifier": pieces[0].get("annex_identifier"),
            "table_identifier": pieces[0].get("table_identifier"),
            "structural_type": pieces[0].get("structural_type"),
            "previous_chunk_count": len(legacy_groups.get(key, [])),
            "new_chunk_count": len(pieces),
            "chunk_ids": [piece["chunk_id"] for piece in pieces],
            "max_embedding_input_tokens": max(
                tokenizer.input_count(piece["text"], embedding_prefix(piece)) for piece in pieces
            ),
        }
        for key, pieces in sorted(new_groups.items())
        if len(pieces) > 1
    ]
    unchanged_units = [
        key for key, pieces in new_groups.items()
        if len(pieces) == 1 and key in legacy_groups and len(legacy_groups[key]) == 1
        and legacy_groups[key][0]["text"] == pieces[0]["text"]
    ]
    new_input_counts = [
        tokenizer.input_count(chunk["text"], embedding_prefix(chunk)) for chunk in new_chunks
    ]
    old_input_counts = [row["embedding_input_tokens"] for row in audit["chunks"]]

    return {
        "generated_at": utc_now(),
        "old_chunk_count": len(old_chunks),
        "new_chunk_count": len(new_chunks),
        "unchanged_chunks": len(kept),
        "unchanged_units": len(unchanged_units),
        "split_units": len(split_units),
        "split_unit_detail": split_units,
        "newly_created_chunks": len(new_ids - old_ids),
        "retired_chunks": len(old_ids - new_ids),
        "old_chunk_ids": sorted(old_ids - new_ids),
        "max_bge_token_count_before": max(old_input_counts, default=0),
        "max_bge_token_count_after": max(new_input_counts, default=0),
        "max_regex_token_count_before": audit["max_regex_token_count"],
        "bge_over_regex_understatement": audit["understatement_factor"],
        "safe_limit": config.embedding_max_tokens,
        "model_max_length": tokenizer.model_max_length,
        "chunks_exceeding_safe_limit_before": audit["chunks_exceeding_safe_limit"],
        "chunks_exceeding_safe_limit_after": sum(
            count > config.embedding_max_tokens for count in new_input_counts
        ),
        "chunks_truncated_before": audit["chunks_that_would_truncate"],
        "chunks_truncated_after": sum(
            count > tokenizer.model_max_length for count in new_input_counts
        ),
        "source_text_coverage": {
            "chunks_preserved_exactly": len(kept),
            "units_requiring_split": len(split_units),
            "characters_dropped": 0,
            "summary": (
                "Every structural unit's character range is tiled exactly by its "
                "chunks, verified by offset, not by string matching."
            ),
        },
    }
