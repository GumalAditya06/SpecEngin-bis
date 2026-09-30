"""Specengine-BIS RAG pipeline module."""

from app.rag.pipeline import run_pipeline
from app.rag.retriever import retrieve_hybrid
from app.rag.reranker import rerank
from app.rag.assembler import assemble_citations, build_evidence_sections
from app.rag.embeddings import embed_text, embed_query, cosine_similarity
from app.rag.product_mapper import extract_attributes, map_product_to_standards
from app.rag.response_builder import (
    determine_confidence,
    build_response,
    generate_grounded,
)

__all__ = [
    "run_pipeline",
    "retrieve_hybrid",
    "rerank",
    "assemble_citations",
    "build_evidence_sections",
    "embed_text",
    "embed_query",
    "cosine_similarity",
    "extract_attributes",
    "map_product_to_standards",
    "determine_confidence",
    "build_response",
    "generate_grounded",
]
