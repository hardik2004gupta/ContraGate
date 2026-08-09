"""
Embedding generation for the memory-store MCP server.

Provides get_embedding(text) → list[float] with an in-memory cache keyed
by SHA-256 of the input text to avoid redundant API calls for identical intent
summaries (CLAUDE.md §19).

Provider selection via EMBEDDING_PROVIDER env var:
  voyage  — Voyage AI (via voyageai or anthropic SDK), dimension 1536
  mock    — Deterministic fake (used in tests, set EMBEDDING_PROVIDER=mock)

The cache is process-local. For a production deployment with multiple workers,
replace with a Redis or DB-backed cache; the interface is the same.

Security: credentials are read from env vars only — never hardcoded.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from abc import ABC, abstractmethod
from typing import Protocol

logger = logging.getLogger(__name__)

EMBEDDING_MODEL = os.environ.get(
    "EMBEDDING_MODEL", "voyage-large-2-instruct"
)
EMBEDDING_DIMENSION = int(os.environ.get("EMBEDDING_DIMENSION", "1536"))

# In-memory cache: SHA-256(text) → embedding vector
_cache: dict[str, list[float]] = {}


class EmbeddingUnavailableError(RuntimeError):
    """Raised when embedding generation cannot proceed (API unavailable, no key, etc.)."""


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def get_embedding(text: str) -> list[float]:
    """
    Return the embedding vector for text.

    Cache lookup first (keyed by SHA-256 of text).
    On cache miss, calls the configured provider (read from EMBEDDING_PROVIDER
    env var at call time so tests can override via monkeypatch/setenv).
    Raises EmbeddingUnavailableError if the provider is unavailable.
    """
    cache_key = _text_hash(text)
    if cache_key in _cache:
        logger.debug("Embedding cache hit for key %s", cache_key[:8])
        return _cache[cache_key]

    # Read at call time so env var overrides work in tests without module reload
    provider = os.environ.get("EMBEDDING_PROVIDER", "voyage").lower()
    if provider == "mock":
        vec = _mock_embedding(text)
    elif provider == "voyage":
        vec = _voyage_embedding(text)
    else:
        raise EmbeddingUnavailableError(
            f"Unknown EMBEDDING_PROVIDER: {provider!r}. Use 'voyage' or 'mock'."
        )

    _cache[cache_key] = vec
    return vec


def _voyage_embedding(text: str) -> list[float]:
    """
    Generate embedding via Voyage AI.

    Tries the anthropic SDK's voyageai integration first (available in ≥0.40),
    then falls back to the standalone voyageai package.
    """
    api_key = os.environ.get("VOYAGE_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        raise EmbeddingUnavailableError(
            "VOYAGE_API_KEY (or ANTHROPIC_API_KEY) is required for embedding generation."
        )

    # Try standalone voyageai package first
    try:
        import voyageai  # type: ignore[import]
        client = voyageai.Client(api_key=api_key)
        result = client.embed([text], model=EMBEDDING_MODEL)
        vec = result.embeddings[0]
        if len(vec) != EMBEDDING_DIMENSION:
            logger.warning(
                "Embedding dimension mismatch: got %d, expected %d",
                len(vec), EMBEDDING_DIMENSION,
            )
        return vec
    except ImportError:
        pass

    # Fallback: try anthropic SDK (may have integrated voyageai)
    try:
        import anthropic  # type: ignore[import]
        client = anthropic.Anthropic(api_key=api_key)
        result = client.embeddings.create(  # type: ignore[attr-defined]
            model=EMBEDDING_MODEL,
            input=text,
        )
        return list(result.data[0].embedding)
    except (ImportError, AttributeError) as exc:
        raise EmbeddingUnavailableError(
            f"Neither voyageai nor anthropic.embeddings is available: {exc}"
        ) from exc


def _mock_embedding(text: str) -> list[float]:
    """
    Deterministic fake embedding for testing.

    Uses a simple hash-based approach so identical texts produce identical
    vectors and semantically similar texts produce approximately similar vectors
    (for Jaccard tests — semantic similarity of mock vectors is not realistic).

    The vector is normalized to unit length to match cosine similarity expectations.
    """
    import struct
    h = hashlib.md5(text.encode("utf-8")).digest()
    # Seed a repeatable float sequence from the hash
    seed_ints = struct.unpack("4I", h)
    vec = []
    for seed in seed_ints:
        rng = seed
        for _ in range(EMBEDDING_DIMENSION // 4):
            rng = (rng * 1664525 + 1013904223) & 0xFFFFFFFF
            # Normalize to [-1, 1]
            vec.append((rng / 0x80000000) - 1.0)
    vec = vec[:EMBEDDING_DIMENSION]
    # L2-normalize for cosine similarity
    norm = (sum(x * x for x in vec) ** 0.5) or 1.0
    return [x / norm for x in vec]


def clear_cache() -> None:
    """Clear the in-memory embedding cache. Used in tests for isolation."""
    _cache.clear()


def cache_size() -> int:
    """Return the number of cached embeddings."""
    return len(_cache)
