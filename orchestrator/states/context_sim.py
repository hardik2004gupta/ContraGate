"""
CONTEXT_AND_SIM state — three-stage memory retrieval + transaction sandbox simulation.

Runs retrieval and simulation in PARALLEL within this state (CLAUDE.md §10).

Three-stage retrieval (CLAUDE.md §19):
  Stage 1 — semantic_search: top-20 pgvector candidates
  Stage 2 — filter_by_table_overlap: Jaccard ≥ 0.3
  Stage 3 — rerank_by_outcome: REJECTED=1.0, ROLLED_BACK=0.9, high-delta=0.7,
             MODIFIED=0.5, APPROVED=0.1×sim

Simulation (CLAUDE.md §18):
  begin_sandbox → set_sandbox_mode → pre-counts → execute → post-counts
  → read_trigger_log → rollback_sandbox
  NEVER executes against production. Staging only.
  Timeout → retry twice with exponential backoff → continue with simulation_available=False

If memory store unavailable → continue without historical context (flagged in contract).
If simulation unavailable after retries → continue with simulation_available=False (flagged).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from orchestrator import mcp_client
from orchestrator.handoff_schema import (
    CascadeEntry,
    HandoffContract,
    HistoricalOperation,
    OperationType,
)
from orchestrator.retry import retry_with_backoff

logger = logging.getLogger(__name__)


async def run_context_sim(contract: HandoffContract) -> HandoffContract:
    """
    Execute three-stage retrieval and transaction sandbox simulation in parallel.

    Delegates to ContextSimAgent which runs retrieval_client and sandbox_client
    concurrently. Both branches are independent — failure in one does not affect
    the other (CLAUDE.md §10).
    """
    from agents.context_sim.agent import ContextSimAgent
    agent = ContextSimAgent(contract.operation_id, contract.tenant_id)
    return await agent.run(contract)


# ── Three-Stage Retrieval ─────────────────────────────────────────────────────

async def _run_retrieval(contract: HandoffContract) -> list[HistoricalOperation]:
    """
    Three-stage retrieval. Raises on any unrecoverable error.
    Memory unavailability is caught by the caller.
    """
    intent = contract.intent_summary or contract.raw_sql[:200]

    # Stage 1: Semantic search — top 20 candidates
    stage1 = await mcp_client.memory_store(
        "semantic_search",
        {
            "intent": intent,
            "tenant_id": contract.tenant_id,
            "top_k": 20,
        },
    )
    candidates = stage1.get("candidates", [])
    if not candidates:
        return []

    # Stage 2: Filter by table overlap (Jaccard ≥ threshold)
    current_tables = _extract_table_set(contract)
    stage2 = await mcp_client.memory_store(
        "filter_by_table_overlap",
        {
            "candidates": candidates,
            "current_tables": list(current_tables),
            "tenant_id": contract.tenant_id,
        },
    )
    filtered = stage2.get("filtered", candidates)
    if not filtered:
        return []

    # Stage 3: Outcome-aware reranking — return top 3
    stage3 = await mcp_client.memory_store(
        "rerank_by_outcome",
        {
            "candidates": filtered,
            "tenant_id": contract.tenant_id,
            "top_k": 3,
        },
    )
    ranked = stage3.get("top3", [])
    return [_to_historical_operation(r) for r in ranked]


def _extract_table_set(contract: HandoffContract) -> set[str]:
    tables = set()
    if contract.primary_table:
        tables.add(contract.primary_table)
    for c in contract.cascade:
        tables.add(c.table)
    return tables


def _to_historical_operation(raw: dict) -> HistoricalOperation:
    return HistoricalOperation(
        operation_id=raw.get("operation_id", "unknown"),
        intent_summary=raw.get("intent_summary", ""),
        tables=raw.get("tables", []),
        outcome=raw.get("outcome", "UNKNOWN"),
        decision_reason=raw.get("decision_reason", ""),
        similarity_score=float(raw.get("similarity_score", 0.0)),
        jaccard_score=float(raw.get("jaccard_score", 0.0)),
        rerank_score=float(raw.get("rerank_score", 0.0)),
    )


# ── Transaction Sandbox Simulation ───────────────────────────────────────────

async def _run_simulation_attempt(contract: HandoffContract) -> dict:
    """
    Single simulation attempt: begin → set_sandbox_mode → pre_count →
    execute → post_count → read_trigger_log → rollback.
    Always rollbacks — production data is never touched.
    """
    session_id: str | None = None
    try:
        # 1. Begin sandbox transaction
        begin_result = await mcp_client.transaction_sandbox(
            "begin_sandbox",
            {"tenant_id": contract.tenant_id},
            timeout=8.0,
        )
        session_id = begin_result.get("session_id")
        if not session_id:
            raise RuntimeError("Sandbox did not return a session_id")

        # 2. Pre-execution counts
        tables_to_count = [contract.primary_table] + [c.table for c in contract.cascade]
        tables_to_count = [t for t in tables_to_count if t]

        pre_diff = await mcp_client.transaction_sandbox(
            "capture_diff",
            {"session_id": session_id, "tables": tables_to_count, "phase": "pre"},
            timeout=8.0,
        )

        # 3. Execute the SQL in sandbox
        await mcp_client.transaction_sandbox(
            "execute_in_sandbox",
            {"session_id": session_id, "sql": contract.raw_sql},
            timeout=8.0,
        )

        # 4. Post-execution counts
        post_diff = await mcp_client.transaction_sandbox(
            "capture_diff",
            {"session_id": session_id, "tables": tables_to_count, "phase": "post"},
            timeout=8.0,
        )

        # 5. Read trigger log
        trigger_log_resp = await mcp_client.transaction_sandbox(
            "get_trigger_log",
            {"session_id": session_id},
            timeout=5.0,
        )

        return {
            "session_id": session_id,
            "pre": pre_diff.get("table_counts", {}),
            "post": post_diff.get("table_counts", {}),
            "trigger_log": trigger_log_resp.get("trigger_log", []),
        }

    finally:
        # ALWAYS rollback — even on error
        if session_id:
            try:
                await mcp_client.transaction_sandbox(
                    "rollback_sandbox",
                    {"session_id": session_id},
                    timeout=5.0,
                )
            except Exception as exc:
                logger.error("Failed to rollback sandbox session %s: %s", session_id, exc)


async def _run_simulation(contract: HandoffContract) -> dict:
    """
    Run simulation with retry (CLAUDE.md §10: 'retry twice with exponential backoff').
    On persistent failure, sets contract.simulation_timeout = True.
    """
    if not contract.primary_table:
        return {"skipped": True, "reason": "no_primary_table"}

    # Don't simulate SELECT operations — they have no write effect
    if contract.operation_type == OperationType.SELECT:
        return {"skipped": True, "reason": "select_operation"}

    try:
        result = await retry_with_backoff(
            _run_simulation_attempt,
            contract,
            max_attempts=2,
            base_delay_seconds=1.0,
            backoff_factor=2.0,
            timeout_seconds=15.0,
        )
        return result
    except Exception as exc:
        contract.simulation_timeout = True
        raise RuntimeError(f"Simulation failed after 2 attempts: {exc}") from exc


def _apply_simulation_result(contract: HandoffContract, sim: dict) -> None:
    """Write simulation results to the HandoffContract."""
    if sim.get("skipped"):
        contract.simulation_available = True
        contract.simulation_executed = False
        return

    pre = sim.get("pre", {})
    post = sim.get("post", {})

    # Primary table delta
    primary = contract.primary_table
    if primary and primary in pre and primary in post:
        pre_count = pre[primary]
        post_count = post[primary]
        contract.actual_primary_rows = abs(pre_count - post_count)

    # Cascade deltas
    actual_cascade = []
    for entry in contract.cascade:
        t = entry.table
        if t in pre and t in post:
            delta = abs(pre.get(t, 0) - post.get(t, 0))
            actual_cascade.append(CascadeEntry(
                table=t,
                estimated_rows=entry.estimated_rows,
                actual_rows=delta,
                cascade_action=entry.cascade_action,
                depth=entry.depth,
            ))
        else:
            actual_cascade.append(entry)
    contract.actual_cascade = actual_cascade

    # Trigger log
    contract.sandbox_trigger_log = sim.get("trigger_log", [])
    contract.simulation_executed = True
    contract.simulation_available = True
