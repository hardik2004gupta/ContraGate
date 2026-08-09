"""
Phase 4 three-stage retrieval — implementation moved to retrieval_client.py.

This module re-exports from retrieval_client for backward compatibility
with any code that imports from retrieval.py directly.
"""

from agents.context_sim.retrieval_client import (  # noqa: F401
    RetrievalClient,
    RetrievalResult,
)
