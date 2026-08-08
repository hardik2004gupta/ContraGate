"""
Sandbox client — typed adapter around the transaction-sandbox MCP server.

Executes the proposed SQL against the WRITABLE STAGING database inside an
explicit transaction that is ALWAYS rolled back (CLAUDE.md §22, invariant 4).

Full simulation sequence:
  1. begin_sandbox    → open transaction, SET LOCAL sandbox_mode and timeout
  2. capture_diff pre → row counts before execution
  3. execute_in_sandbox → run the SQL (staging only)
  4. capture_diff post → row counts after execution
  5. get_trigger_log  → external calls that WOULD have fired
  6. rollback_sandbox → ROLLBACK (staging data never permanently changes)

The transaction-sandbox MCP server owns: connection, transaction lifecycle,
sandbox variables, trigger log. This client owns: typed calls, operation
identity, result mapping, error handling.

CRITICAL INVARIANTS:
  Invariant 4: NEVER executes against production database.
  Invariant 5: No network egress from sandbox (infrastructure-level block).
  Rollback is mandatory — called even if execution raises an exception.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from orchestrator.handoff_schema import CascadeEntry, HandoffContract, OperationType

logger = logging.getLogger(__name__)


class SimulationTimeoutError(RuntimeError):
    """Raised when the sandbox statement_timeout fires."""


class SimulationFailedError(RuntimeError):
    """Raised when the sandbox execution fails for any non-timeout reason."""


@dataclass
class SimulationResult:
    """Typed output from one sandbox simulation run."""
    executed: bool = False
    available: bool = True
    timeout_occurred: bool = False
    actual_primary_rows: int | None = None
    actual_cascade: list[CascadeEntry] = field(default_factory=list)
    trigger_log: list[dict[str, Any]] = field(default_factory=list)
    failure_reason: str | None = None
    skipped_reason: str | None = None


class SandboxClient:
    """
    Thin typed adapter for transaction sandbox simulation via MCP.

    Instantiated per-operation. All calls go through call_tool for audit logging.
    """

    def __init__(self, call_tool_fn) -> None:
        self._call_tool = call_tool_fn

    async def simulate(self, contract: HandoffContract) -> SimulationResult:
        """
        Execute the proposed SQL in the sandbox and return the result.

        Does NOT raise. Failures return SimulationResult(available=False).
        Rollback is always performed — even on error.
        """
        # Skip SELECT operations — they have no write effect to measure
        if contract.operation_type == OperationType.SELECT:
            return SimulationResult(
                executed=False,
                available=True,
                skipped_reason="select_operation",
            )

        if not contract.primary_table:
            return SimulationResult(
                executed=False,
                available=True,
                skipped_reason="no_primary_table",
            )

        session_id: str | None = None
        try:
            result = await self._run_once(contract)
            return result

        except SimulationTimeoutError as exc:
            logger.warning(
                "Sandbox timeout for operation %s: %s",
                contract.operation_id, exc,
            )
            return SimulationResult(
                executed=False,
                available=False,
                timeout_occurred=True,
                failure_reason=str(exc),
            )

        except Exception as exc:
            logger.warning(
                "Simulation failed for operation %s: %s",
                contract.operation_id, exc,
            )
            return SimulationResult(
                executed=False,
                available=False,
                failure_reason=str(exc),
            )

    async def _run_once(self, contract: HandoffContract) -> SimulationResult:
        """
        Execute one simulation attempt. Rollback is guaranteed via finally.
        Raises SimulationTimeoutError on timeout, SimulationFailedError on other DB errors.
        """
        session_id: str | None = None
        try:
            # 1. Open sandbox transaction (sets statement_timeout and sandbox_mode)
            begin_resp = await self._call_tool(
                "transaction-sandbox",
                "begin_sandbox",
                {"tenant_id": contract.tenant_id},
            )
            session_id = begin_resp.get("session_id")
            if not session_id:
                raise SimulationFailedError("Sandbox did not return a session_id")

            # 2. Tables to count: primary + cascade targets
            tables_to_count = [contract.primary_table] + [
                c.table for c in contract.cascade if c.table
            ]
            tables_to_count = list(dict.fromkeys(tables_to_count))  # dedup, preserve order

            # 3. Pre-execution row counts
            pre_resp = await self._call_tool(
                "transaction-sandbox",
                "capture_diff",
                {"session_id": session_id, "tables": tables_to_count, "phase": "pre"},
            )
            pre_counts = pre_resp.get("table_counts", {})

            # 4. Execute SQL in sandbox
            try:
                exec_resp = await self._call_tool(
                    "transaction-sandbox",
                    "execute_in_sandbox",
                    {"session_id": session_id, "sql": contract.raw_sql},
                )
            except Exception as exc:
                err = str(exc)
                if "statement_timeout" in err.lower() or "canceling statement" in err.lower():
                    raise SimulationTimeoutError(
                        f"Sandbox statement_timeout during execution: {exc}"
                    ) from exc
                raise SimulationFailedError(f"SQL execution in sandbox failed: {exc}") from exc

            # 5. Post-execution row counts
            post_resp = await self._call_tool(
                "transaction-sandbox",
                "capture_diff",
                {"session_id": session_id, "tables": tables_to_count, "phase": "post"},
            )
            post_counts = post_resp.get("table_counts", {})

            # 6. Read trigger log (external calls that WOULD have fired)
            trigger_resp = await self._call_tool(
                "transaction-sandbox",
                "get_trigger_log",
                {"session_id": session_id},
            )
            trigger_log = trigger_resp.get("trigger_log", [])

            # Build result from pre/post deltas
            result = self._build_result(
                contract, pre_counts, post_counts, trigger_log
            )
            return result

        finally:
            # Rollback is ALWAYS called — production data never changes
            if session_id:
                try:
                    await self._call_tool(
                        "transaction-sandbox",
                        "rollback_sandbox",
                        {"session_id": session_id},
                    )
                    logger.debug("Sandbox rolled back for session %s", session_id)
                except Exception as rb_exc:
                    logger.error(
                        "Failed to rollback sandbox session %s: %s",
                        session_id, rb_exc,
                    )

    def _build_result(
        self,
        contract: HandoffContract,
        pre: dict[str, int],
        post: dict[str, int],
        trigger_log: list[dict],
    ) -> SimulationResult:
        """Compute actual row counts from pre/post diffs."""
        primary = contract.primary_table

        # Primary table actual rows
        actual_primary: int | None = None
        if primary and primary in pre and primary in post:
            pre_val = pre[primary]
            post_val = post[primary]
            if pre_val >= 0 and post_val >= 0:
                actual_primary = abs(pre_val - post_val)

        # Cascade table deltas
        actual_cascade: list[CascadeEntry] = []
        for entry in contract.cascade:
            t = entry.table
            if t in pre and t in post and pre[t] >= 0 and post[t] >= 0:
                delta = abs(pre[t] - post[t])
                actual_cascade.append(CascadeEntry(
                    table=t,
                    estimated_rows=entry.estimated_rows,
                    actual_rows=delta,
                    cascade_action=entry.cascade_action,
                    depth=entry.depth,
                ))
            else:
                # Cannot determine actual rows — keep estimated, no actual
                actual_cascade.append(entry)

        return SimulationResult(
            executed=True,
            available=True,
            actual_primary_rows=actual_primary,
            actual_cascade=actual_cascade,
            trigger_log=trigger_log,
        )

    def apply_to_contract(
        self, contract: HandoffContract, result: SimulationResult
    ) -> None:
        """Write simulation results into the HandoffContract."""
        contract.simulation_available = result.available
        contract.simulation_executed = result.executed
        contract.simulation_timeout = result.timeout_occurred

        if result.executed:
            contract.actual_primary_rows = result.actual_primary_rows
            contract.actual_cascade = result.actual_cascade
            contract.sandbox_trigger_log = result.trigger_log
