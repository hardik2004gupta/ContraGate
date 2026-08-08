"""
Unit tests for agents/context_sim/sandbox_client.py.

All tests use mocked call_tool — no transaction-sandbox MCP connections.
Tests verify:
  - Full simulation sequence (begin → pre → execute → post → trigger → rollback)
  - Rollback is always called (success, failure, timeout)
  - SELECT and no-table operations are skipped
  - Timeout detection
  - Row delta computation
  - SimulationResult fields
  - apply_to_contract field mapping
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, call

from orchestrator.handoff_schema import (
    CascadeEntry,
    HandoffContract,
    OperationType,
    SourceType,
)
from agents.context_sim.sandbox_client import (
    SandboxClient,
    SimulationFailedError,
    SimulationResult,
    SimulationTimeoutError,
)


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_test_sandbox",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        intent_summary="Delete inactive user accounts",
        primary_table="users",
        operation_type=OperationType.DELETE,
        cascade=[
            CascadeEntry(table="orders", estimated_rows=5000,
                         cascade_action="CASCADE", depth=1),
        ],
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


def _build_mock_sequence(
    session_id="sess-abc-123",
    pre_counts={"users": 50000, "orders": 180000},
    post_counts={"users": 45800, "orders": 174500},
    trigger_log=None,
    exec_raises=None,
):
    """Build a mock call_tool that produces a successful simulation sequence."""
    responses = [
        {"session_id": session_id, "transaction_open": True},  # begin_sandbox
        {"table_counts": pre_counts},                            # capture_diff pre
    ]
    if exec_raises:
        async def side_effect(server, tool, args, **kw):
            call_count = side_effect.counter
            side_effect.counter += 1
            if call_count < 2:
                return responses[call_count]
            if call_count == 2:
                raise exec_raises
            return {"rolled_back": True}
        side_effect.counter = 0
        return AsyncMock(side_effect=side_effect)

    responses += [
        {"rows_affected": 4200, "status": "executed"},          # execute_in_sandbox
        {"table_counts": post_counts},                           # capture_diff post
        {"trigger_log": trigger_log or []},                      # get_trigger_log
        {"rolled_back": True},                                   # rollback_sandbox
    ]
    return AsyncMock(side_effect=responses)


class TestSandboxClientSuccess:
    @pytest.mark.asyncio
    async def test_full_sequence_calls_all_tools(self):
        mock_call = _build_mock_sequence()
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert result.executed is True
        assert result.available is True

        tools_called = [c[0][1] for c in mock_call.call_args_list]
        assert "begin_sandbox" in tools_called
        assert "capture_diff" in tools_called
        assert "execute_in_sandbox" in tools_called
        assert "get_trigger_log" in tools_called
        assert "rollback_sandbox" in tools_called

    @pytest.mark.asyncio
    async def test_actual_primary_rows_computed(self):
        # 50000 before, 45800 after → 4200 affected
        mock_call = _build_mock_sequence(
            pre_counts={"users": 50000, "orders": 180000},
            post_counts={"users": 45800, "orders": 174500},
        )
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert result.actual_primary_rows == 4200

    @pytest.mark.asyncio
    async def test_cascade_rows_computed(self):
        # orders: 180000 before, 174500 after → 5500 affected
        mock_call = _build_mock_sequence(
            pre_counts={"users": 50000, "orders": 180000},
            post_counts={"users": 45800, "orders": 174500},
        )
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        cascade = result.actual_cascade
        assert len(cascade) == 1
        assert cascade[0].table == "orders"
        assert cascade[0].actual_rows == 5500

    @pytest.mark.asyncio
    async def test_trigger_log_captured(self):
        trigger_entry = {
            "trigger_name": "notify_user_delete",
            "table_name": "users",
            "operation": "DELETE",
            "target_url": "https://sendgrid.example.com/unsubscribe",
            "payload_summary": "unsubscribe_batch",
            "estimated_calls": 4200,
        }
        mock_call = _build_mock_sequence(trigger_log=[trigger_entry])
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert len(result.trigger_log) == 1
        assert result.trigger_log[0]["trigger_name"] == "notify_user_delete"

    @pytest.mark.asyncio
    async def test_rollback_always_called_on_success(self):
        mock_call = _build_mock_sequence()
        client = SandboxClient(call_tool_fn=mock_call)
        await client.simulate(_make_contract())

        rollback_calls = [
            c for c in mock_call.call_args_list
            if c[0][1] == "rollback_sandbox"
        ]
        assert len(rollback_calls) == 1

    @pytest.mark.asyncio
    async def test_tenant_id_passed_to_begin(self):
        mock_call = _build_mock_sequence()
        client = SandboxClient(call_tool_fn=mock_call)
        await client.simulate(_make_contract(tenant_id="tenant_xyz"))

        begin_args = mock_call.call_args_list[0][0][2]
        assert begin_args["tenant_id"] == "tenant_xyz"


class TestSandboxSkippedOperations:
    @pytest.mark.asyncio
    async def test_select_operation_skipped(self):
        mock_call = AsyncMock()
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(
            _make_contract(operation_type=OperationType.SELECT)
        )

        assert result.executed is False
        assert result.available is True
        assert result.skipped_reason == "select_operation"
        mock_call.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_primary_table_skipped(self):
        mock_call = AsyncMock()
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(
            _make_contract(primary_table="", cascade=[])
        )

        assert result.executed is False
        assert result.available is True
        assert result.skipped_reason == "no_primary_table"
        mock_call.assert_not_called()


class TestRollbackOnFailure:
    @pytest.mark.asyncio
    async def test_rollback_called_when_execute_fails(self):
        mock_call = _build_mock_sequence(exec_raises=RuntimeError("SQL error"))
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert result.executed is False
        assert result.available is False

        rollback_calls = [
            c for c in mock_call.call_args_list
            if c[0][1] == "rollback_sandbox"
        ]
        assert len(rollback_calls) == 1

    @pytest.mark.asyncio
    async def test_timeout_detected_from_error_message(self):
        timeout_exc = RuntimeError("ERROR: canceling statement due to statement_timeout")
        mock_call = _build_mock_sequence(exec_raises=timeout_exc)
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert result.available is False
        assert result.timeout_occurred is True

    @pytest.mark.asyncio
    async def test_begin_failure_returns_unavailable(self):
        mock_call = AsyncMock(side_effect=RuntimeError("DB connection refused"))
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert result.available is False
        assert result.executed is False
        assert "DB connection refused" in result.failure_reason


class TestApplyToContract:
    def test_applies_successful_simulation(self):
        cascade = [
            CascadeEntry(table="orders", estimated_rows=5000,
                         actual_rows=5500, cascade_action="CASCADE", depth=1)
        ]
        sim = SimulationResult(
            executed=True, available=True,
            actual_primary_rows=4200,
            actual_cascade=cascade,
            trigger_log=[{"trigger": "sendgrid_webhook"}],
        )
        contract = _make_contract()
        client = SandboxClient(call_tool_fn=AsyncMock())
        client.apply_to_contract(contract, sim)

        assert contract.simulation_executed is True
        assert contract.simulation_available is True
        assert contract.actual_primary_rows == 4200
        assert len(contract.actual_cascade) == 1
        assert contract.actual_cascade[0].actual_rows == 5500
        assert len(contract.sandbox_trigger_log) == 1

    def test_applies_failed_simulation(self):
        sim = SimulationResult(
            executed=False, available=False,
            timeout_occurred=True,
        )
        contract = _make_contract()
        client = SandboxClient(call_tool_fn=AsyncMock())
        client.apply_to_contract(contract, sim)

        assert contract.simulation_available is False
        assert contract.simulation_executed is False
        assert contract.simulation_timeout is True
        assert contract.actual_primary_rows is None
