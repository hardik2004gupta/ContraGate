"""
Unit tests for the embedding layer (mcp_servers/memory_store/embeddings.py).

All tests use EMBEDDING_PROVIDER=mock — no Anthropic API calls.
"""

from __future__ import annotations

import hashlib
import os
from unittest.mock import patch

import pytest

from mcp_servers.memory_store.embeddings import (
    EMBEDDING_DIMENSION,
    EmbeddingUnavailableError,
    _mock_embedding,
    cache_size,
    clear_cache,
    get_embedding,
)


@pytest.fixture(autouse=True)
def isolate_cache_and_env(monkeypatch):
    """Clear the embedding cache and force mock provider before every test."""
    monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")
    clear_cache()
    yield
    clear_cache()


class TestGetEmbeddingMock:
    def test_returns_list_of_floats(self):
        vec = get_embedding("delete users where inactive")
        assert isinstance(vec, list)
        assert all(isinstance(x, float) for x in vec)

    def test_dimension_matches_configured(self):
        vec = get_embedding("test intent summary")
        assert len(vec) == EMBEDDING_DIMENSION

    def test_identical_text_identical_vector(self):
        text = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        v1 = get_embedding(text)
        v2 = get_embedding(text)
        assert v1 == v2

    def test_different_text_different_vector(self):
        v1 = get_embedding("delete users")
        v2 = get_embedding("update orders")
        assert v1 != v2

    def test_vector_is_normalized(self):
        vec = get_embedding("some intent text for normalization test")
        norm = sum(x * x for x in vec) ** 0.5
        assert abs(norm - 1.0) < 1e-6, f"Expected unit norm, got {norm}"


class TestEmbeddingCache:
    def test_cache_starts_empty(self):
        assert cache_size() == 0

    def test_cache_populated_after_first_call(self):
        get_embedding("first call populates cache")
        assert cache_size() == 1

    def test_second_call_uses_cache(self):
        """_mock_embedding should only be called once for the same text."""
        call_count = 0
        original_mock = _mock_embedding

        def counting_mock(text):
            nonlocal call_count
            call_count += 1
            return original_mock(text)

        with patch("mcp_servers.memory_store.embeddings._mock_embedding",
                   side_effect=counting_mock):
            text = "cacheable intent text for cache test"
            get_embedding(text)
            get_embedding(text)  # Second call — should hit cache

        assert call_count == 1

    def test_different_texts_separate_cache_entries(self):
        get_embedding("intent A")
        get_embedding("intent B")
        get_embedding("intent C")
        assert cache_size() == 3

    def test_clear_cache_empties_store(self):
        get_embedding("first")
        get_embedding("second")
        clear_cache()
        assert cache_size() == 0

    def test_cache_key_is_sha256_of_text(self):
        text = "known text for key test"
        get_embedding(text)
        expected_key = hashlib.sha256(text.encode("utf-8")).hexdigest()
        from mcp_servers.memory_store.embeddings import _cache
        assert expected_key in _cache


class TestEmbeddingUnavailable:
    def test_unknown_provider_raises(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_PROVIDER", "unknown_provider_xyz")
        clear_cache()
        with pytest.raises(EmbeddingUnavailableError, match="Unknown EMBEDDING_PROVIDER"):
            get_embedding("test text for unknown provider")

    def test_voyage_provider_without_key_raises(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_PROVIDER", "voyage")
        monkeypatch.delenv("VOYAGE_API_KEY", raising=False)
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        clear_cache()
        with pytest.raises(EmbeddingUnavailableError):
            get_embedding("test text for voyage without key")
