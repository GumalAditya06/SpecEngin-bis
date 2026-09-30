"""Stage 3.2 entry point for the retrieval sanity report.

Kept separate from ``retrieval.py`` so the CLI and the evaluation script load
the same model exactly once and share one code path for querying.
"""

from __future__ import annotations

from pathlib import Path

from .config import Settings
from .retrieval import (
    DEFAULT_TOP_K,
    RETRIEVAL_REPORT_FILENAME,
    run_retrieval_sanity,
)


def evaluate(
    settings: Settings,
    store_dir: Path | None = None,
    embeddings_path: Path | None = None,
    top_k: int = DEFAULT_TOP_K,
    output_path: Path | None = None,
) -> dict:
    """Build the retrieval sanity report and write it next to the index."""
    store_dir = store_dir or settings.vector_store_dir
    embeddings_path = embeddings_path or (settings.embeddings_v2_dir / "embeddings.jsonl")
    output_path = output_path or (store_dir / RETRIEVAL_REPORT_FILENAME)
    # run_retrieval_sanity loads the model itself, so it is loaded once here
    # rather than twice.
    return run_retrieval_sanity(
        settings,
        store_dir=store_dir,
        embeddings_path=embeddings_path,
        top_k=top_k,
        output_path=output_path,
    )
