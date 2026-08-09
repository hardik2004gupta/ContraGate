"""
Unit tests for agents/context_sim/agent.py (ContextSimAgent).

All MCP calls are mocked. Tests verify:
  - Retrieval and simulation start in parallel (not sequential)
  - Retrieval failure keeps simulation results
  - Simulation failure keeps retrieval results
  - Both succeed → contract updated correctly
  - Provenance recorded with CONTEXT_SIM_AGENT and llm_involved=False
  - INSERT sequence_gap_warning set correctly
  - No Analyzer-owned fields overwritten
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orchestrator.handoff_schema import (
    CascadeEntry,
    HandoffContract,
    HistoricalOperation,
    OperationType,
    SourceType,
)


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_test_csagent",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        intent_summary="Delete inactive users older than 2 years",
        primary_table="users",
        operation_type=OperationType.DELETE,
        estimated_primary_rows=4200,
        cascade=[
            CascadeEntry(table="orders", estimated_rows=5000,
                         cascade_action="CASCADE", depth=1),
        ],
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


def _historical_op() -> HistoricalOperation:
    return HistoricalOperation(
        operation_id="cg_1847",
        intent_summary="Delete inactive user accounts older than 2 years",
        tables=["users", "orders", "invoices"],
        outcome="REJECTED",
        decision_reason="Cascade into invoices caused loss of 8,200 billing records.",
        similarity_score=0.91,
        jaccard_score=0.75,
        rerank_score=1.0,
    )


class TestContextSimAgentParallelism:
    """
    Verify retrieval and simulation run in parallel (not sequentially).
    Both tasks should start before either completes.
    """

    @pytest.mark.asyncio
    async def test_both_tasks_start_concurrently(self):
        """
        If sequential: total ≈ delay_a + delay_b.
        If parallel:   total ≈ max(delay_a, delay_b).
        We use 100ms delays — if sequential, total ≈ 200ms; if parallel, ≈ 100ms.
        """
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.retrieval_client import RetrievalResult
        from agents.context_sim.sandbox_client import SimulationResult

        async def slow_retrieve(contract):
            await asyncio.sleep(0.1)
            return RetrievalResult(
                precedents=[_historical_op()],
                stage1_candidates=1,
                stage2_candidates=1,
                available=True,
            )

        async def slow_sim_with_retry(contract):
            await asyncio.sleep(0.1)
            return SimulationResult(
                executed=True, available=True,
                actual_primary_rows=4200,
            )

        agent = ContextSimAgent("cg_test_csagent", "demo_tenant")

        with (
            patch.object(agent._retrieval, "retrieve", side_effect=slow_retrieve),
            patch.object(agent, "_run_simulation_with_retry",
                         side_effect=slow_sim_with_retry),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            start = time.monotonic()
            contract = _make_contract()
            await agent.run(contract)
            elapsed = time.monotonic() - start

        # If truly parallel: ≈0.1s. If sequential: ≈0.2s. Allow up to 0.18s.
        assert elapsed < 0.18, (
            f"Tasks appear sequential: elapsed={elapsed:.3f}s (expected < 0.18s for parallel)"
        )


class TestContextSimAgentBothSucceed:
    @pytest.mark.asyncio
    async def test_precedents_in_contract(self):
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.retrieval_client import RetrievalResult
        from agents.context_sim.sandbox_client import SimulationResult

        prec = _historical_op()
        retrieval_result = RetrievalResult(
            precedents=[prec], stage1_candidates=5,
            stage2_candidates=2, available=True,
        )
        sim_result = SimulationResult(
            executed=True, available=True,
            actual_primary_rows=4200,
            actual_cascade=[
                CascadeEntry(table="orders", estimated_rows=5000,
                             actual_rows=5500, cascade_action="CASCADE", depth=1)
            ],
            trigger_log=[],
        )

        agent = ContextSimAgent("cg_test_csagent", "demo_tenant")
        with (
            patch.object(agent._retrieval, "retrieve", new=AsyncMock(return_value=retrieval_result)),
            patch.object(agent, "_run_simulation_with_retry",
                         new=AsyncMock(return_value=sim_result)),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            contract = _make_contract()
            await agent.run(contract)

        assert contract.retrieval_available is True
        assert len(contract.historical_precedents) == 1
        assert contract.historical_precedents[0].operation_id == "cg_1847"
        assert contract.simulation_executed is True
        assert contract.actual_primary_rows == 4200

    @pytest.mark.asyncio
    async def test_provenance_added_with_correct_agent_name(self):
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.retrieval_client import RetrievalResult
        from agents.context_sim.sandbox_client import SimulationResult

        retrieval_result = RetrievalResult(precedents=[], stage1_candidates=0,
                                           stage2_candidates=0, available=True)
        sim_result = SimulationResult(executed=False, available=True, skipped_reason="select_operation")

        agent = ContextSimAgent("cg_test", "demo_tenant")
        with (
            patch.object(agent._retrieval, "retrieve", new=AsyncMock(return_value=retrieval_result)),
            patch.object(agent, "_run_simulation_with_retry",
                         new=AsyncMock(return_value=sim_result)),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            contract = _make_contract()
            await agent.run(contract)

        provenance_agents = [p.agent for p in contract.workflow_provenance]
        assert "CONTEXT_SIM_AGENT" in provenance_agents

        ctx_prov = next(p for p in contract.workflow_provenance if p.agent == "CONTEXT_SIM_AGENT")
        assert ctx_prov.llm_involved is False

    @pytest.mark.asyncio
    async def test_analyzer_fields_not_overwritten(self):
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.retrieval_client import RetrievalResult
        from agents.context_sim.sandbox_client import SimulationResult

        retrieval_result = RetrievalResult(precedents=[], stage1_candidates=0,
                                           stage2_candidates=0, available=True)
        sim_result = SimulationResult(executed=False, available=True, skipped_reason="no_primary_table")

        agent = ContextSimAgent("cg_test", "demo_tenant")
        contract = _make_contract(
            intent_summary="Original analyzer summary",
            estimated_primary_rows=9999,
        )
        with (
            patch.object(agent._retrieval, "retrieve", new=AsyncMock(return_value=retrieval_result)),
            patch.object(agent, "_run_simulation_with_retry",
                         new=AsyncMock(return_value=sim_result)),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            await agent.run(contract)

        # Analyzer-owned fields must be unchanged
        assert contract.intent_summary == "Original analyzer summary"
        assert contract.estimated_primary_rows == 9999
        assert contract.operation_type == OperationType.DELETE


class TestContextSimAgentPartialFailure:
    @pytest.mark.asyncio
    async def test_retrieval_fail_simulation_succeeds(self):
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.retrieval_client import RetrievalResult
        from agents.context_sim.sandbox_client import SimulationResult

        # Retrieval returns unavailable
        retrieval_result = RetrievalResult(
            precedents=[], stage1_candidates=0, stage2_candidates=0,
            available=False, failure_reason="pgvector down",
        )
        sim_result = SimulationResult(
            executed=True, available=True, actual_primary_rows=4200
        )

        agent = ContextSimAgent("cg_test", "demo_tenant")
        with (
            patch.object(agent._retrieval, "retrieve", new=AsyncMock(return_value=retrieval_result)),
            patch.object(agent, "_run_simulation_with_retry",
                         new=AsyncMock(return_value=sim_result)),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            contract = _make_contract()
            await agent.run(contract)

        # Retrieval failed
        assert contract.retrieval_available is False
        assert contract.historical_precedents == []
        # Simulation still valid
        assert contract.simulation_executed is True
        assert contract.actual_primary_rows == 4200

    @pytest.mark.asyncio
    async def test_simulation_fail_retrieval_succeeds(self):
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.retrieval_client import RetrievalResult
        from agents.context_sim.sandbox_client import SimulationResult

        retrieval_result = RetrievalResult(
            precedents=[_historical_op()], stage1_candidates=3,
            stage2_candidates=2, available=True,
        )
        sim_result = SimulationResult(
            executed=False, available=False,
            timeout_occurred=True,
            failure_reason="Simulation unavailable after 2 attempts",
        )

        agent = ContextSimAgent("cg_test", "demo_tenant")
        with (
            patch.object(agent._retrieval, "retrieve", new=AsyncMock(return_value=retrieval_result)),
            patch.object(agent, "_run_simulation_with_retry",
                         new=AsyncMock(return_value=sim_result)),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            contract = _make_contract()
            await agent.run(contract)

        # Simulation failed
        assert contract.simulation_available is False
        assert contract.simulation_executed is False
        # Retrieval still valid
        assert contract.retrieval_available is True
        assert len(contract.historical_precedents) == 1

    @pytest.mark.asyncio
    async def test_retrieval_raises_exception_handled(self):
        """Even if retrieval raises (not just returns unavailable), agent handles it."""
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.sandbox_client import SimulationResult

        sim_result = SimulationResult(executed=True, available=True, actual_primary_rows=100)

        agent = ContextSimAgent("cg_test", "demo_tenant")
        with (
            patch.object(agent._retrieval, "retrieve",
                         new=AsyncMock(side_effect=RuntimeError("unexpected crash"))),
            patch.object(agent, "_run_simulation_with_retry",
                         new=AsyncMock(return_value=sim_result)),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            contract = _make_contract()
            await agent.run(contract)

        assert contract.retrieval_available is False
        # Simulation still applied
        assert contract.simulation_executed is True


class TestInsertSequenceGapWarning:
    @pytest.mark.asyncio
    async def test_insert_sets_sequence_gap_warning(self):
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.retrieval_client import RetrievalResult
        from agents.context_sim.sandbox_client import SimulationResult

        retrieval_result = RetrievalResult(precedents=[], stage1_candidates=0,
                                           stage2_candidates=0, available=True)
        sim_result = SimulationResult(executed=True, available=True, actual_primary_rows=50)

        agent = ContextSimAgent("cg_test", "demo_tenant")
        with (
            patch.object(agent._retrieval, "retrieve", new=AsyncMock(return_value=retrieval_result)),
            patch.object(agent, "_run_simulation_with_retry",
                         new=AsyncMock(return_value=sim_result)),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            contract = _make_contract(operation_type=OperationType.INSERT)
            await agent.run(contract)

        assert contract.sequence_gap_warning is True

    @pytest.mark.asyncio
    async def test_delete_does_not_set_sequence_gap_warning(self):
        from agents.context_sim.agent import ContextSimAgent
        from agents.context_sim.retrieval_client import RetrievalResult
        from agents.context_sim.sandbox_client import SimulationResult

        retrieval_result = RetrievalResult(precedents=[], stage1_candidates=0,
                                           stage2_candidates=0, available=True)
        sim_result = SimulationResult(executed=True, available=True, actual_primary_rows=4200)

        agent = ContextSimAgent("cg_test", "demo_tenant")
        with (
            patch.object(agent._retrieval, "retrieve", new=AsyncMock(return_value=retrieval_result)),
            patch.object(agent, "_run_simulation_with_retry",
                         new=AsyncMock(return_value=sim_result)),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            contract = _make_contract(operation_type=OperationType.DELETE)
            await agent.run(contract)

        assert contract.sequence_gap_warning is False
