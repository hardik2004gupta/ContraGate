"""
Context and Simulation Agent — Phase 4 implementation.

Retrieves historically similar operations from memory AND simulates the proposed
operation against real staging data. Both tasks run IN PARALLEL (CLAUDE.md §10).

Architecture (CLAUDE.md §8 — Agent 2):
  retrieval_client.py → memory-store MCP → pgvector three-stage retrieval
  sandbox_client.py   → transaction-sandbox MCP → staging execute + ROLLBACK

Security invariants enforced here:
  Invariant 4: Simulation NEVER touches production (MCP boundary enforces this).
  Invariant 6: Every memory query is tenant-scoped (MCP boundary enforces this).
  Invariant 14: Provenance is recorded on every contract field write.
  No LLM calls in this agent — all operations are deterministic.

Failure handling:
  Retrieval failure  → retrieval_available=False, historical_precedents=[]
  Simulation failure → simulation_available=False, simulation_executed=False
  One branch failing does NOT affect the other (asyncio.gather with return_exceptions)
  Sandbox timeout retry: handled by orchestrator/retry.py at the state level.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime

from agents.base_agent import BaseAgent
from agents.context_sim.retrieval_client import RetrievalClient
from agents.context_sim.sandbox_client import SandboxClient
from mcp_servers.audit_logger.checksum import hash_payload
from orchestrator.handoff_schema import HandoffContract
from orchestrator.retry import retry_with_backoff

logger = logging.getLogger(__name__)

# Fields owned by the Context + Simulation Agent (must not overwrite Analyzer fields)
_CONTEXT_SIM_FIELDS = frozenset({
    "retrieval_available",
    "historical_precedents",
    "simulation_available",
    "simulation_executed",
    "actual_primary_rows",
    "actual_cascade",
    "sandbox_trigger_log",
    "simulation_timeout",
    "sequence_gap_warning",
})


class ContextSimAgent(BaseAgent):
    """
    Context and Simulation Agent.

    Runs retrieval and simulation in parallel within this agent.
    Writes only the Context/Simulation fields of the HandoffContract.
    Does NOT re-run Analyzer-owned fields (intent_summary, operation_type, etc.).
    No LLM calls.
    """

    AGENT_NAME = "CONTEXT_SIM_AGENT"

    def __init__(self, operation_id: str, tenant_id: str) -> None:
        super().__init__(operation_id, tenant_id)
        self._retrieval = RetrievalClient(call_tool_fn=self.call_tool)
        self._sandbox = SandboxClient(call_tool_fn=self.call_tool)

    async def run(self, contract: HandoffContract) -> HandoffContract:
        """
        Run retrieval and simulation in parallel, then populate the HandoffContract.

        Returns the updated HandoffContract.
        """
        start = datetime.utcnow()
        logger.info(
            "CONTEXT_SIM_AGENT starting",
            extra={
                "operation_id": contract.operation_id,
                "primary_table": contract.primary_table,
                "operation_type": contract.operation_type,
            },
        )

        # Launch both tasks in parallel — asyncio.gather with return_exceptions=True
        # ensures a failure in one does not cancel the other.
        retrieval_task = asyncio.create_task(self._retrieval.retrieve(contract))
        simulation_task = asyncio.create_task(
            self._run_simulation_with_retry(contract)
        )

        retrieval_result, simulation_result = await asyncio.gather(
            retrieval_task, simulation_task, return_exceptions=True
        )

        # Apply retrieval results (errors already converted to RetrievalResult.available=False)
        if isinstance(retrieval_result, Exception):
            logger.warning(
                "Retrieval raised unexpectedly for %s: %s",
                contract.operation_id, retrieval_result,
            )
            contract.retrieval_available = False
            contract.historical_precedents = []
        else:
            self._retrieval.apply_to_contract(contract, retrieval_result)

        # Apply simulation results (errors already converted to SimulationResult.available=False)
        if isinstance(simulation_result, Exception):
            logger.warning(
                "Simulation raised unexpectedly for %s: %s",
                contract.operation_id, simulation_result,
            )
            contract.simulation_available = False
            contract.simulation_executed = False
        else:
            self._sandbox.apply_to_contract(contract, simulation_result)

        # sequence_gap_warning: INSERT-based sequences accumulate gaps during
        # sandbox execution (sequences are not rolled back). Flag if the operation
        # is an INSERT — gap is low-severity, informational only.
        from orchestrator.handoff_schema import OperationType
        if contract.operation_type == OperationType.INSERT and contract.simulation_executed:
            contract.sequence_gap_warning = True

        elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000

        contract.add_provenance(
            agent=self.AGENT_NAME,
            field_written=(
                "retrieval_available,historical_precedents,"
                "simulation_available,simulation_executed,"
                "actual_primary_rows,actual_cascade,sandbox_trigger_log,"
                "simulation_timeout,sequence_gap_warning"
            ),
            llm_involved=False,
        )

        # Audit the final agent output (non-blocking, failure is non-fatal)
        contract_hash = hash_payload(contract.model_dump())
        await self.log_agent_output(
            output_summary=(
                f"retrieval={'OK' if contract.retrieval_available else 'FAIL'} "
                f"precedents={len(contract.historical_precedents)} "
                f"simulation={'OK' if contract.simulation_executed else 'SKIP/FAIL'} "
                f"actual_rows={contract.actual_primary_rows}"
            ),
            contract_hash=contract_hash,
        )

        logger.info(
            "CONTEXT_SIM_AGENT complete",
            extra={
                "operation_id": contract.operation_id,
                "retrieval_available": contract.retrieval_available,
                "precedent_count": len(contract.historical_precedents),
                "simulation_executed": contract.simulation_executed,
                "actual_primary_rows": contract.actual_primary_rows,
                "elapsed_ms": round(elapsed_ms, 1),
            },
        )
        return contract

    async def _run_simulation_with_retry(
        self, contract: HandoffContract
    ):
        """
        Run simulation with exponential backoff retry (CLAUDE.md §10).
        Two attempts maximum. On persistent failure, sets simulation_timeout.
        """
        from agents.context_sim.sandbox_client import SimulationResult, SimulationTimeoutError

        for attempt in range(1, 3):
            result = await self._sandbox.simulate(contract)
            if result.available or result.skipped_reason:
                return result
            if result.timeout_occurred and attempt < 2:
                wait_s = 1.0 * (2 ** (attempt - 1))
                logger.warning(
                    "Sandbox timeout on attempt %d for %s, retrying in %.1fs",
                    attempt, contract.operation_id, wait_s,
                )
                await asyncio.sleep(wait_s)
            else:
                # Either not a timeout, or second timeout — give up
                break

        # Mark timeout on the contract before returning failed result
        contract.simulation_timeout = True
        from agents.context_sim.sandbox_client import SimulationResult
        return SimulationResult(
            executed=False,
            available=False,
            timeout_occurred=True,
            failure_reason="Simulation unavailable after 2 attempts",
        )
