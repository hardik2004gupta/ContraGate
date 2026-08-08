"""
Phase 6 — test_idempotency.py

Verifies execution idempotency:
- workflow_store.execution_completed flag prevents duplicate execution
- set_execution_result marks the record as completed
- Second execution call returns early without calling MCP target
- The flag persists across workflow_store.get() calls
"""

from __future__ import annotations

import pytest

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    SourceType,
)
from orchestrator.workflow_store import WorkflowRecord, WorkflowStatus, WorkflowStore


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_idem00001",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE status = 'inactive'",
        operation_type=OperationType.DELETE,
        approval_state=ApprovalState.APPROVED,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


@pytest.mark.asyncio
class TestWorkflowStoreIdempotency:
    async def test_execution_completed_initially_false(self):
        store = WorkflowStore()
        contract = _make_contract()
        record = await store.create(contract, {"sql": contract.raw_sql})
        assert record.execution_completed is False

    async def test_set_execution_result_marks_completed(self):
        store = WorkflowStore()
        contract = _make_contract()
        await store.create(contract, {"sql": contract.raw_sql})

        await store.set_execution_result(
            contract.operation_id,
            {"success": True, "result": {"rows_deleted": 42}},
        )
        record = await store.get(contract.operation_id)
        assert record.execution_completed is True

    async def test_execution_result_stored(self):
        store = WorkflowStore()
        contract = _make_contract()
        await store.create(contract, {"sql": contract.raw_sql})

        result_payload = {"success": True, "rows_affected": 100}
        await store.set_execution_result(contract.operation_id, result_payload)
        record = await store.get(contract.operation_id)
        assert record.execution_result == result_payload

    async def test_completed_flag_persists_after_get(self):
        store = WorkflowStore()
        contract = _make_contract()
        await store.create(contract, {"sql": contract.raw_sql})
        await store.set_execution_result(contract.operation_id, {"success": True})

        # Get twice — flag must persist
        record1 = await store.get(contract.operation_id)
        record2 = await store.get(contract.operation_id)
        assert record1.execution_completed is True
        assert record2.execution_completed is True

    async def test_status_updated_to_completed_after_execution(self):
        store = WorkflowStore()
        contract = _make_contract()
        await store.create(contract, {"sql": contract.raw_sql})
        await store.set_execution_result(contract.operation_id, {"success": True})
        await store.update_status(contract.operation_id, WorkflowStatus.COMPLETED)
        record = await store.get(contract.operation_id)
        assert record.status == WorkflowStatus.COMPLETED


@pytest.mark.asyncio
class TestExecutionIdempotencyGuard:
    async def test_execution_skipped_when_already_completed(self):
        """run_execution() returns early if execution_completed is True."""
        from unittest.mock import AsyncMock, patch

        contract = _make_contract()

        store = WorkflowStore()
        await store.create(contract, {"sql": contract.raw_sql})
        await store.set_execution_result(contract.operation_id, {"success": True})

        call_count = {"n": 0}

        async def mock_call(*args, **kwargs):
            call_count["n"] += 1
            return {"rows_deleted": 42}

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=mock_call),
        ):
            from orchestrator.states.execution import run_execution
            result = await run_execution(contract)

        # MCP target should NOT have been called — idempotency guard blocked it
        assert call_count["n"] == 0
        assert result.execution_success is True

    async def test_execution_proceeds_when_not_completed(self):
        """run_execution() calls MCP target when execution_completed is False."""
        from unittest.mock import AsyncMock, patch

        contract = _make_contract()

        store = WorkflowStore()
        manifest = {
            "tool_name": "execute_query",
            "sql": contract.raw_sql,
            "target_mcp_url": "http://test-mcp:8010",
        }
        await store.create(contract, manifest)

        call_count = {"n": 0}

        async def mock_call(*args, **kwargs):
            call_count["n"] += 1
            return {"rows_deleted": 42}

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=mock_call),
        ):
            from orchestrator.states.execution import run_execution
            result = await run_execution(contract)

        assert call_count["n"] == 1
        assert result.execution_success is True
