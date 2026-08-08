"""
Context and Simulation Agent — orchestration.

PHASE 0 SCAFFOLD — NOT IMPLEMENTED.
Implementation begins in Phase 4.

Responsibilities (CLAUDE.md §8 — Agent 2):
- Retrieve historically similar operations from memory (retrieval.py)
- Simulate proposed operation against staging data (sandbox.py)
- Retrieval and simulation run IN PARALLEL within this agent
- Write retrieval_results and simulation to HandoffContract

Failure handling (CLAUDE.md §10 — guard conditions):
- Memory store unavailable → continue with retrieval_available = False (flagged in contract)
- Sandbox timeout → retry twice with exponential backoff →
    continue with simulation_available = False (flagged in contract)
"""
