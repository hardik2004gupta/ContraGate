"""
Phase 4 transaction sandbox — implementation moved to sandbox_client.py.

This module re-exports from sandbox_client for backward compatibility
with any code that imports from sandbox.py directly.
"""

from agents.context_sim.sandbox_client import (  # noqa: F401
    SandboxClient,
    SimulationFailedError,
    SimulationResult,
    SimulationTimeoutError,
)
