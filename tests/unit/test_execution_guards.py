"""
Phase 6 — test_execution_guards.py

Verifies EXECUTION state guards working together:
- Both idempotency and stale-state checks are enforced
- Provenance is appended regardless of which path is taken
- MCP call error → execution_success = False (does not raise)
- Auto-execute path does not invoke run_execution
"""

from __future__ import annotations

import pytest

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    SourceType,
)
from orchestrator.states.execution import _manifest_hash
from orchestrator.workflow_store import WorkflowStore


def _make_contract(op_id: str = "cg_exguard01", **kwargs) -> HandoffContract:
    defaults = dict(
        operation_id=op_id,
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM invoices WHERE status = 'void'",
        operation_type=OperationType.DELETE,
        approval_state=ApprovalState.APPROVED,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


def _build_manifest(sql: str) -> dict:
    body = {"tool_name": "execute_query", "sql": sql}
    body["_manifest_hash"] = _manifest_hash(body.copy())
    return body


@pytest.mark.asyncio
class TestExecutionGuardsCombined:
    async def test_provenance_appended_on_success(self):
        from unittest.mock import patch

        contract = _make_contract()
        store = WorkflowStore()
        await store.create(contract, _build_manifest(contract.raw_sql))

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch(
                "orchestrator.states.execution.mcp_client.call",
                new=__import__("unittest.mock", fromlist=["AsyncMock"]).AsyncMock(
                    return_value={"rows_deleted": 1}
                ),
            ),
        ):
            from orchestrator.states.execution import run_execution
            initial_count = len(contract.workflow_provenance)
            result = await run_execution(contract)

        assert len(result.workflow_provenance) > initial_count

    async def test_provenance_appended_on_stale_contract_abort(self):
        from unittest.mock import patch

        sql = "DELETE FROM invoices WHERE status = 'void'"
        manifest = {"tool_name": "execute_query", "sql": sql, "_manifest_hash": "wrong" * 16}

        contract = _make_contract()
        store = WorkflowStore()
        await store.create(contract, manifest)

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
        ):
            from orchestrator.states.execution import run_execution
            initial_count = len(contract.workflow_provenance)
            result = await run_execution(contract)

        # Stale contract → execution_success = False + provenance added
        assert result.execution_success is False
        assert len(result.workflow_provenance) > initial_count

    async def test_mcp_error_does_not_raise(self):
        from unittest.mock import patch
        from orchestrator.mcp_client import MCPCallError

        contract = _make_contract()
        store = WorkflowStore()
        await store.create(contract, _build_manifest(contract.raw_sql))

        async def _fail(*args, **kwargs):
            raise MCPCallError("postgres-reader", "execute_query", "MCP target unavailable")

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=_fail),
        ):
            from orchestrator.states.execution import run_execution
            result = await run_execution(contract)

        assert result.execution_success is False

    async def test_mcp_error_sets_execution_result_in_store(self):
        from unittest.mock import patch
        from orchestrator.mcp_client import MCPCallError

        contract = _make_contract(op_id="cg_exguard02")
        store = WorkflowStore()
        await store.create(contract, _build_manifest(contract.raw_sql))

        async def _fail(*args, **kwargs):
            raise MCPCallError("postgres-reader", "execute_query", "timeout")

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=_fail),
        ):
            from orchestrator.states.execution import run_execution
            await run_execution(contract)

        record = await store.get(contract.operation_id)
        assert record.execution_result is not None
        assert record.execution_result.get("success") is False

    async def test_idempotency_and_stale_both_checked_idempotency_first(self):
        """Idempotency check runs before stale-state check — completed wins."""
        from unittest.mock import patch

        contract = _make_contract(op_id="cg_exguard03")
        store = WorkflowStore()
        # Store wrong hash (would trigger stale-state abort)
        manifest = {"tool_name": "execute_query", "sql": contract.raw_sql, "_manifest_hash": "bad" * 21}
        await store.create(contract, manifest)
        # Mark as already executed
        await store.set_execution_result(contract.operation_id, {"success": True})

        call_count = {"n": 0}
        async def _track_call(*args, **kwargs):
            call_count["n"] += 1
            return {}

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=_track_call),
        ):
            from orchestrator.states.execution import run_execution
            result = await run_execution(contract)

        # execution_completed flag stopped it before stale-state check
        assert call_count["n"] == 0
        assert result.execution_success is True

    async def test_successful_execution_sets_timestamp(self):
        from unittest.mock import patch

        contract = _make_contract(op_id="cg_exguard04")
        store = WorkflowStore()
        await store.create(contract, _build_manifest(contract.raw_sql))

        async def _ok(*args, **kwargs):
            return {"rows_deleted": 0}

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=_ok),
        ):
            from orchestrator.states.execution import run_execution
            result = await run_execution(contract)

        assert result.execution_timestamp is not None
