"""
Phase 6 — test_human_review.py

Verifies the HUMAN_REVIEW state:
- 30-minute timeout (injectable via CONTRAGATE_TIMEOUT_SECONDS)
- Decision polling stops when workflow_store returns a non-PENDING state
- Decision fields are copied from the stored contract
- Timeout path sets approval_state = TIMED_OUT
- Notifier failure is non-fatal
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    RiskTier,
    SourceType,
)
from orchestrator.workflow_store import WorkflowRecord, WorkflowStatus


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_hr000001",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        operation_type=OperationType.DELETE,
        risk_tier=RiskTier.FULL_CONTRACT,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


def _make_record_with_decision(contract: HandoffContract, decision: str, reason: str) -> WorkflowRecord:
    """Build a WorkflowRecord that looks like a decision was already made."""
    decided = _make_contract(
        operation_id=contract.operation_id,
        approval_state=(
            ApprovalState.APPROVED if decision == "APPROVE"
            else ApprovalState.MODIFIED if decision == "MODIFY"
            else ApprovalState.REJECTED
        ),
        human_decision=decision,
        decision_reason=reason,
        decision_timestamp=datetime.now(timezone.utc),
    )
    record = WorkflowRecord(contract.operation_id, decided)
    return record


# The workflow_store singleton is imported lazily inside run_human_review(),
# so we must patch it at its SOURCE location, not at the states module level.
_STORE_GET = "orchestrator.workflow_store.workflow_store.get"


@pytest.mark.asyncio
class TestHumanReviewTimeout:
    async def test_timeout_sets_timed_out_state(self):
        """When no decision arrives before timeout, state is TIMED_OUT."""
        contract = _make_contract()

        pending_record = WorkflowRecord(contract.operation_id, contract)
        pending_record.contract.approval_state = ApprovalState.PENDING

        with (
            patch("orchestrator.states.human_review.REVIEW_TIMEOUT_SECONDS", 0.01),
            patch("orchestrator.states.human_review.POLL_INTERVAL_SECONDS", 0.001),
            patch("orchestrator.states.human_review.mcp_client.notifier", new=AsyncMock()),
            patch(_STORE_GET, new=AsyncMock(return_value=pending_record)),
        ):
            from orchestrator.states.human_review import run_human_review
            result = await run_human_review(contract)

        assert result.approval_state == ApprovalState.TIMED_OUT
        assert result.decision_timestamp is not None
        assert "timed out" in result.decision_reason.lower()

    async def test_timeout_injectable_via_module_attribute(self):
        """REVIEW_TIMEOUT_SECONDS can be patched for fast test execution."""
        contract = _make_contract()
        pending_record = WorkflowRecord(contract.operation_id, contract)

        import orchestrator.states.human_review as hr_module
        original_timeout = hr_module.REVIEW_TIMEOUT_SECONDS
        original_poll = hr_module.POLL_INTERVAL_SECONDS

        try:
            hr_module.REVIEW_TIMEOUT_SECONDS = 0.01
            hr_module.POLL_INTERVAL_SECONDS = 0.001

            with (
                patch("orchestrator.states.human_review.mcp_client.notifier", new=AsyncMock()),
                patch(_STORE_GET, new=AsyncMock(return_value=pending_record)),
            ):
                result = await hr_module.run_human_review(contract)
        finally:
            hr_module.REVIEW_TIMEOUT_SECONDS = original_timeout
            hr_module.POLL_INTERVAL_SECONDS = original_poll

        assert result.approval_state == ApprovalState.TIMED_OUT


@pytest.mark.asyncio
class TestHumanReviewDecision:
    async def test_approved_decision_stops_polling(self):
        """When workflow_store returns APPROVED, polling stops and state is copied."""
        contract = _make_contract()
        decided_record = _make_record_with_decision(contract, "APPROVE", "Approved after review.")

        with (
            patch("orchestrator.states.human_review.REVIEW_TIMEOUT_SECONDS", 10.0),
            patch("orchestrator.states.human_review.POLL_INTERVAL_SECONDS", 0.001),
            patch("orchestrator.states.human_review.mcp_client.notifier", new=AsyncMock()),
            patch(_STORE_GET, new=AsyncMock(return_value=decided_record)),
        ):
            from orchestrator.states.human_review import run_human_review
            result = await run_human_review(contract)

        assert result.approval_state == ApprovalState.APPROVED
        assert result.human_decision == "APPROVE"
        assert "Approved after review" in result.decision_reason

    async def test_rejected_decision_copied_to_contract(self):
        contract = _make_contract()
        decided_record = _make_record_with_decision(contract, "REJECT", "Cascade risk too high.")

        with (
            patch("orchestrator.states.human_review.REVIEW_TIMEOUT_SECONDS", 10.0),
            patch("orchestrator.states.human_review.POLL_INTERVAL_SECONDS", 0.001),
            patch("orchestrator.states.human_review.mcp_client.notifier", new=AsyncMock()),
            patch(_STORE_GET, new=AsyncMock(return_value=decided_record)),
        ):
            from orchestrator.states.human_review import run_human_review
            result = await run_human_review(contract)

        assert result.approval_state == ApprovalState.REJECTED
        assert "Cascade" in result.decision_reason

    async def test_modified_decision_copied_to_contract(self):
        contract = _make_contract()
        decided_record = _make_record_with_decision(contract, "MODIFY", "Limit to 1000 rows.")

        with (
            patch("orchestrator.states.human_review.REVIEW_TIMEOUT_SECONDS", 10.0),
            patch("orchestrator.states.human_review.POLL_INTERVAL_SECONDS", 0.001),
            patch("orchestrator.states.human_review.mcp_client.notifier", new=AsyncMock()),
            patch(_STORE_GET, new=AsyncMock(return_value=decided_record)),
        ):
            from orchestrator.states.human_review import run_human_review
            result = await run_human_review(contract)

        assert result.approval_state == ApprovalState.MODIFIED

    async def test_notifier_failure_is_non_fatal(self):
        """If notifier throws, human_review continues anyway."""
        from orchestrator.mcp_client import MCPCallError

        contract = _make_contract()
        decided_record = _make_record_with_decision(contract, "APPROVE", "Approved after review.")

        with (
            patch("orchestrator.states.human_review.REVIEW_TIMEOUT_SECONDS", 10.0),
            patch("orchestrator.states.human_review.POLL_INTERVAL_SECONDS", 0.001),
            patch(
                "orchestrator.states.human_review.mcp_client.notifier",
                new=AsyncMock(
                    side_effect=MCPCallError("notifier", "send_approval_request", "down")
                ),
            ),
            patch(_STORE_GET, new=AsyncMock(return_value=decided_record)),
        ):
            from orchestrator.states.human_review import run_human_review
            result = await run_human_review(contract)

        # Should complete without raising despite notifier failure
        assert result.approval_state == ApprovalState.APPROVED

    async def test_provenance_appended_after_decision(self):
        contract = _make_contract()
        decided_record = _make_record_with_decision(contract, "APPROVE", "Approved after review.")
        initial_provenance_count = len(contract.workflow_provenance)

        with (
            patch("orchestrator.states.human_review.REVIEW_TIMEOUT_SECONDS", 10.0),
            patch("orchestrator.states.human_review.POLL_INTERVAL_SECONDS", 0.001),
            patch("orchestrator.states.human_review.mcp_client.notifier", new=AsyncMock()),
            patch(_STORE_GET, new=AsyncMock(return_value=decided_record)),
        ):
            from orchestrator.states.human_review import run_human_review
            result = await run_human_review(contract)

        assert len(result.workflow_provenance) > initial_provenance_count
