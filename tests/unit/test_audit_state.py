"""
Phase 6 — test_audit_state.py

Verifies the AUDIT state:
- _determine_outcome() maps all approval states correctly
- Blast radius accuracy delta is computed when actual rows are known
- Feedback loop stub runs without raising
- Audit never raises even when MCP calls fail
- Provenance is appended
- Memory write-back and audit-logger calls are made (or gracefully degraded)
"""

from __future__ import annotations

import pytest

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    SourceType,
)
from orchestrator.states.audit import (
    _build_audit_payload,
    _determine_outcome,
    _trigger_feedback_loop_stub,
)


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_audit0001",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        operation_type=OperationType.DELETE,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


# ── _determine_outcome ────────────────────────────────────────────────────────

class TestDetermineOutcome:
    def test_auto_rejected_state(self):
        c = _make_contract(
            approval_state=ApprovalState.AUTO_REJECTED,
            auto_reject_triggered=True,
        )
        assert _determine_outcome(c) == "AUTO_REJECTED"

    def test_auto_reject_flag_overrides_state(self):
        # Even if state is PENDING but flag is set, outcome is AUTO_REJECTED
        c = _make_contract(auto_reject_triggered=True)
        assert _determine_outcome(c) == "AUTO_REJECTED"

    def test_timed_out_state(self):
        c = _make_contract(approval_state=ApprovalState.TIMED_OUT)
        assert _determine_outcome(c) == "TIMED_OUT"

    def test_rejected_state(self):
        c = _make_contract(approval_state=ApprovalState.REJECTED)
        assert _determine_outcome(c) == "REJECTED"

    def test_modified_state(self):
        c = _make_contract(approval_state=ApprovalState.MODIFIED)
        assert _determine_outcome(c) == "MODIFIED"

    def test_approved_and_executed(self):
        c = _make_contract(
            approval_state=ApprovalState.APPROVED,
            execution_success=True,
        )
        assert _determine_outcome(c) == "APPROVED_EXECUTED"

    def test_approved_but_execution_failed(self):
        c = _make_contract(
            approval_state=ApprovalState.APPROVED,
            execution_success=False,
        )
        assert _determine_outcome(c) == "APPROVED_EXECUTION_FAILED"

    def test_unknown_state_returns_unknown(self):
        c = _make_contract(approval_state=ApprovalState.PENDING)
        assert _determine_outcome(c) == "UNKNOWN"


# ── _trigger_feedback_loop_stub ───────────────────────────────────────────────

class TestFeedbackLoopStub:
    def test_delta_computed_when_actual_rows_known(self):
        c = _make_contract(
            estimated_primary_rows=1000,
            actual_primary_rows=1200,  # 20% overrun
        )
        _trigger_feedback_loop_stub(c)
        assert c.blast_radius_accuracy_delta is not None
        assert abs(c.blast_radius_accuracy_delta - 0.2) < 0.001

    def test_delta_computed_for_underestimate(self):
        c = _make_contract(
            estimated_primary_rows=1000,
            actual_primary_rows=800,  # 20% undercount
        )
        _trigger_feedback_loop_stub(c)
        assert c.blast_radius_accuracy_delta == pytest.approx(-0.2, abs=1e-6)

    def test_no_delta_when_actual_rows_unknown(self):
        c = _make_contract(estimated_primary_rows=1000, actual_primary_rows=None)
        _trigger_feedback_loop_stub(c)
        assert c.blast_radius_accuracy_delta is None

    def test_no_delta_when_estimated_rows_zero(self):
        c = _make_contract(estimated_primary_rows=0, actual_primary_rows=100)
        _trigger_feedback_loop_stub(c)
        assert c.blast_radius_accuracy_delta is None

    def test_stub_does_not_raise(self):
        c = _make_contract()
        _trigger_feedback_loop_stub(c)  # should not raise


# ── _build_audit_payload ──────────────────────────────────────────────────────

class TestBuildAuditPayload:
    def test_contains_required_fields(self):
        c = _make_contract(approval_state=ApprovalState.REJECTED)
        payload = _build_audit_payload(c, "REJECTED")
        assert payload["operation_id"] == c.operation_id
        assert payload["tenant_id"] == c.tenant_id
        assert payload["outcome"] == "REJECTED"
        assert "operation_type" in payload
        assert "contract_json" in payload

    def test_contract_json_is_parseable(self):
        import json

        c = _make_contract(approval_state=ApprovalState.APPROVED, execution_success=True)
        payload = _build_audit_payload(c, "APPROVED_EXECUTED")
        parsed = json.loads(payload["contract_json"])
        assert parsed["operation_id"] == c.operation_id

    def test_blast_radius_delta_included(self):
        c = _make_contract(blast_radius_accuracy_delta=0.25)
        payload = _build_audit_payload(c, "REJECTED")
        assert payload["blast_radius_delta"] == 0.25


# ── run_audit failure resilience ──────────────────────────────────────────────

@pytest.mark.asyncio
class TestAuditFailureResilience:
    async def test_audit_does_not_raise_on_mcp_failure(self):
        from unittest.mock import AsyncMock, patch
        from orchestrator.mcp_client import MCPCallError

        contract = _make_contract(approval_state=ApprovalState.REJECTED)

        async def _fail(*args, **kwargs):
            raise MCPCallError("audit-logger", "log_auto_reject", "server unavailable")

        with (
            patch("orchestrator.states.audit.mcp_client.audit_logger", new=_fail),
            patch("orchestrator.states.audit.mcp_client.memory_store", new=_fail),
        ):
            from orchestrator.states.audit import run_audit
            result = await run_audit(contract)

        # Must complete without raising
        assert result is contract

    async def test_audit_appends_provenance(self):
        from unittest.mock import AsyncMock, patch

        contract = _make_contract(approval_state=ApprovalState.APPROVED, execution_success=True)
        initial_count = len(contract.workflow_provenance)

        with (
            patch("orchestrator.states.audit.mcp_client.audit_logger", new=AsyncMock()),
            patch("orchestrator.states.audit.mcp_client.memory_store", new=AsyncMock()),
        ):
            from orchestrator.states.audit import run_audit
            result = await run_audit(contract)

        assert len(result.workflow_provenance) > initial_count
        agents = [p.agent for p in result.workflow_provenance]
        assert "AUDIT" in agents
