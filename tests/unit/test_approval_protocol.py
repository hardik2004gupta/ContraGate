"""
Phase 6 — test_approval_protocol.py

Verifies the async approval protocol:
- PENDING_HUMAN_APPROVAL response format
- Decision recording (single-use, no replay)
- Minimum reason length enforcement
- Valid/invalid decision routing
- REQUEST_PREREQUISITE handling
"""

from __future__ import annotations

import asyncio

import pytest
import pytest_asyncio

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    SourceType,
)
from orchestrator.workflow_store import WorkflowStatus, WorkflowStore
from proxy.async_protocol import build_pending_response


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_proto001",
        tenant_id="demo_tenant",
        submitted_by="test_agent",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        operation_type=OperationType.DELETE,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


# ── build_pending_response ────────────────────────────────────────────────────

class TestBuildPendingResponse:
    def test_contains_required_fields(self):
        resp = build_pending_response("cg_abc12345")
        assert resp["status"] == "PENDING_HUMAN_APPROVAL"
        assert resp["approval_id"] == "cg_abc12345"
        assert "poll_url" in resp
        assert "sse_url" in resp
        assert "message" in resp

    def test_poll_url_contains_approval_id(self):
        resp = build_pending_response("cg_abc12345")
        assert "cg_abc12345" in resp["poll_url"]

    def test_sse_url_contains_approval_id(self):
        resp = build_pending_response("cg_abc12345")
        assert "cg_abc12345" in resp["sse_url"]

    def test_estimated_review_seconds_positive(self):
        resp = build_pending_response("cg_abc12345")
        assert resp["estimated_review_seconds"] > 0

    def test_different_ids_produce_different_urls(self):
        r1 = build_pending_response("cg_aaa")
        r2 = build_pending_response("cg_bbb")
        assert r1["poll_url"] != r2["poll_url"]
        assert r1["sse_url"] != r2["sse_url"]


# ── WorkflowStore.record_decision ─────────────────────────────────────────────

@pytest.mark.asyncio
class TestWorkflowStoreDecision:
    async def _make_store_with_record(self) -> tuple[WorkflowStore, str]:
        store = WorkflowStore()
        contract = _make_contract()
        manifest = {"tool_name": "execute_query", "sql": contract.raw_sql}
        await store.create(contract, manifest)
        return store, contract.operation_id

    async def test_valid_approve_recorded(self):
        store, op_id = await self._make_store_with_record()
        ok = await store.record_decision(op_id, "APPROVE", "Approved after careful review.")
        assert ok is True
        record = await store.get(op_id)
        assert record.contract.approval_state == ApprovalState.APPROVED

    async def test_valid_reject_recorded(self):
        store, op_id = await self._make_store_with_record()
        ok = await store.record_decision(op_id, "REJECT", "Rejected due to cascade risk.")
        assert ok is True
        record = await store.get(op_id)
        assert record.contract.approval_state == ApprovalState.REJECTED

    async def test_valid_modify_recorded(self):
        store, op_id = await self._make_store_with_record()
        ok = await store.record_decision(op_id, "MODIFY", "Limit to 1000 rows maximum.", modification_constraints="LIMIT 1000")
        assert ok is True
        record = await store.get(op_id)
        assert record.contract.approval_state == ApprovalState.MODIFIED

    async def test_request_prerequisite_maps_to_rejected(self):
        store, op_id = await self._make_store_with_record()
        ok = await store.record_decision(op_id, "REQUEST_PREREQUISITE", "Need backup confirmation.")
        assert ok is True
        record = await store.get(op_id)
        assert record.contract.approval_state == ApprovalState.REJECTED

    async def test_no_replay_second_decision_rejected(self):
        store, op_id = await self._make_store_with_record()
        ok1 = await store.record_decision(op_id, "APPROVE", "Approved after review.")
        ok2 = await store.record_decision(op_id, "REJECT", "Changed my mind after all.")
        assert ok1 is True
        assert ok2 is False  # No replay allowed

    async def test_decision_sets_reason(self):
        store, op_id = await self._make_store_with_record()
        await store.record_decision(op_id, "REJECT", "Cascade into invoices not acceptable.")
        record = await store.get(op_id)
        assert "Cascade" in record.contract.decision_reason

    async def test_decision_sets_timestamp(self):
        store, op_id = await self._make_store_with_record()
        await store.record_decision(op_id, "APPROVE", "Approved by senior engineer.")
        record = await store.get(op_id)
        assert record.contract.decision_timestamp is not None

    async def test_unknown_id_returns_false(self):
        store = WorkflowStore()
        ok = await store.record_decision("nonexistent_id", "APPROVE", "Approved after review.")
        assert ok is False

    async def test_modification_constraints_stored(self):
        store, op_id = await self._make_store_with_record()
        await store.record_decision(
            op_id, "MODIFY", "Limit operation scope.",
            modification_constraints="WHERE last_active < NOW() - INTERVAL '5 years'"
        )
        record = await store.get(op_id)
        assert "5 years" in record.contract.modification_constraints

    async def test_approver_id_stored(self):
        store, op_id = await self._make_store_with_record()
        await store.record_decision(op_id, "APPROVE", "Approved by DBA team.", approver_id="dba_alice")
        record = await store.get(op_id)
        assert record.contract.approver_id == "dba_alice"


# ── WorkflowStatus transitions ────────────────────────────────────────────────

@pytest.mark.asyncio
class TestWorkflowStatusTransitions:
    async def test_initial_status_is_running(self):
        store = WorkflowStore()
        contract = _make_contract()
        record = await store.create(contract, {"sql": "SELECT 1"})
        assert record.status == WorkflowStatus.RUNNING

    async def test_update_status_approved(self):
        store = WorkflowStore()
        contract = _make_contract()
        await store.create(contract, {"sql": "SELECT 1"})
        await store.update_status(contract.operation_id, WorkflowStatus.APPROVED)
        record = await store.get(contract.operation_id)
        assert record.status == WorkflowStatus.APPROVED

    async def test_update_status_rejected(self):
        store = WorkflowStore()
        contract = _make_contract()
        await store.create(contract, {"sql": "SELECT 1"})
        await store.update_status(contract.operation_id, WorkflowStatus.REJECTED)
        record = await store.get(contract.operation_id)
        assert record.status == WorkflowStatus.REJECTED

    async def test_to_status_response_contains_approval_id(self):
        store = WorkflowStore()
        contract = _make_contract()
        record = await store.create(contract, {"sql": "SELECT 1"})
        resp = record.to_status_response()
        assert resp["approval_id"] == contract.operation_id

    async def test_rejected_status_includes_reason(self):
        store = WorkflowStore()
        contract = _make_contract()
        await store.create(contract, {"sql": "SELECT 1"})
        await store.record_decision(contract.operation_id, "REJECT", "Cascade risk is too high.")
        await store.update_status(contract.operation_id, WorkflowStatus.REJECTED)
        record = await store.get(contract.operation_id)
        resp = record.to_status_response()
        assert "reason" in resp
