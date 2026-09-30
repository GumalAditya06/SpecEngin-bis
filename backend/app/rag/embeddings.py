"""Deterministic local embeddings — no external API required.

Strategy: hashed bag-of-words. Each token is hashed into the embedding
space with a signed hash; dimensionality is EMBEDDING_DIM (matching the
pgvector column). Vectors are L2-normalised so cosine similarity is a dot
product.

This is a *semantic* approximation only — it captures lexical overlap, not
deep semantics — but it makes the pgvector retrieval branch work end-to-end
locally with zero external dependencies. Swap `embed_text` for a real
embedding model later: the interface (str → list[float], dim EMBEDDING_DIM)
stays the same.
"""

import hashlib
import math
import re

from app.models.base import EMBEDDING_DIM

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_STOPWORDS = frozenset(
    "a an and are as at be by for from in into is it of on or that the to with what which who how why when where".split()
)

# Public alias for other retrieval stages (keyword branch filtering).
STOPWORDS = _STOPWORDS


def _tokenize(text: str) -> list[str]:
    return [
        t
        for t in _TOKEN_RE.findall(text.lower())
        if t not in _STOPWORDS and len(t) > 1
    ]


def _signed_hash(token: str, salt: int) -> int:
    """Deterministic signed hash of a token into [0, dim) × {+1, -1}."""
    digest = hashlib.blake2b(
        f"{salt}:{token}".encode("utf-8"), digest_size=8
    ).digest()
    value = int.from_bytes(digest, "big")
    index = value % EMBEDDING_DIM
    sign = 1 if (value >> 63) & 1 else -1
    return index if sign > 0 else ~index  # ~i encodes negative bucket


def embed_text(text: str) -> list[float]:
    """Embed text into a fixed-size L2-normalised vector."""
    vec = [0.0] * EMBEDDING_DIM
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for token in tokens:
        for salt in (0, 1):  # two hash functions reduce collisions
            h = _signed_hash(token, salt)
            if h >= 0:
                vec[h] += 1.0
            else:
                vec[~h] -= 1.0
    norm = math.sqrt(sum(v * v for v in vec))
    if norm > 0:
        vec = [v / norm for v in vec]
    return vec


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Cosine similarity for two equal-length vectors (0.0 if either is empty)."""
    if not a or not b:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return 0.0
    return dot / (na * nb)


def embed_query(text: str) -> list[float]:
    """Embed a retrieval query. Same model as chunks for this local scheme."""
    return embed_text(text)
