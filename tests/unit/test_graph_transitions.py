"""
Phase 6 — test_graph_transitions.py

Verifies all LangGraph state routing decisions via the guard functions.
All tests are pure — no MCP calls, no database access.
"""

from __future__ import annotations

import pytest

from orchestrator.guards import (
    _is_fast_path,
    human_approved,
    human_modified,
    human_rejected,
    needs_selective_reanalysis,
    requires_full_contract_by_history,
    requires_full_contract_by_reversibility,
    route_from_human_review,
    route_from_risk_gate,
    sandbox_retry_needed,
    should_auto_execute,
    should_auto_reject,
    timed_out,
)
from orchestrator.handoff_schema import (
    ApprovalState,
    CascadeEntry,
    HandoffContract,
    HistoricalOperation,
    OperationType,
    PolicyViolation,
    ReversibilityClass,
    RiskTier,
    SourceType,
)


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_test0001",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="SELECT 1",
        operation_type=OperationType.SELECT,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


# ── route_from_risk_gate ──────────────────────────────────────────────────────

class TestRouteFromRiskGate:
    def test_auto_reject_on_hard_violation(self):
        c = _make_contract(auto_reject_triggered=True)
        assert route_from_risk_gate(c) == "auto_reject"

    def test_auto_reject_takes_priority_over_fast_path(self):
        # Even if it looks like a fast-path SELECT, auto_reject wins
        c = _make_contract(
            operation_type=OperationType.SELECT,
            auto_reject_triggered=True,
            risk_score=0.0,
        )
        assert route_from_risk_gate(c) == "auto_reject"

    def test_auto_execute_for_clean_select(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            risk_score=0.05,
        )
        assert route_from_risk_gate(c) == "auto_execute"

    def test_full_pipeline_for_write_operation(self):
        c = _make_contract(operation_type=OperationType.DELETE)
        assert route_from_risk_gate(c) == "full_pipeline"

    def test_full_pipeline_for_select_with_violations(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            policy_violations=[
                PolicyViolation(
                    rule_id="POLICY_PII",
                    rule_name="PII Table",
                    severity="SOFT",
                    description="PII table involved",
                )
            ],
        )
        assert route_from_risk_gate(c) == "full_pipeline"

    def test_full_pipeline_for_high_risk_score(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            risk_score=0.5,
        )
        assert route_from_risk_gate(c) == "full_pipeline"

    def test_full_pipeline_when_policy_requires_tier(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            required_tier_from_policy="FULL_CONTRACT",
            risk_score=0.0,
        )
        assert route_from_risk_gate(c) == "full_pipeline"


# ── _is_fast_path ─────────────────────────────────────────────────────────────

class TestIsFastPath:
    def test_clean_select_is_fast_path(self):
        c = _make_contract(operation_type=OperationType.SELECT, risk_score=0.05)
        assert _is_fast_path(c) is True

    def test_delete_is_not_fast_path(self):
        c = _make_contract(operation_type=OperationType.DELETE)
        assert _is_fast_path(c) is False

    def test_update_is_not_fast_path(self):
        c = _make_contract(operation_type=OperationType.UPDATE)
        assert _is_fast_path(c) is False

    def test_insert_is_not_fast_path(self):
        c = _make_contract(operation_type=OperationType.INSERT)
        assert _is_fast_path(c) is False

    def test_select_with_violations_not_fast_path(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            policy_violations=[
                PolicyViolation(
                    rule_id="POLICY_PII",
                    rule_name="PII",
                    severity="SOFT",
                    description="PII",
                )
            ],
        )
        assert _is_fast_path(c) is False

    def test_select_high_risk_score_not_fast_path(self):
        c = _make_contract(operation_type=OperationType.SELECT, risk_score=0.3)
        assert _is_fast_path(c) is False

    def test_select_with_required_policy_tier_not_fast_path(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            required_tier_from_policy="STANDARD_REVIEW",
        )
        assert _is_fast_path(c) is False


# ── route_from_human_review ───────────────────────────────────────────────────

class TestRouteFromHumanReview:
    def test_approved_routes_to_execution(self):
        c = _make_contract(approval_state=ApprovalState.APPROVED)
        assert route_from_human_review(c) == "approved"

    def test_rejected_routes_to_audit(self):
        c = _make_contract(approval_state=ApprovalState.REJECTED)
        assert route_from_human_review(c) == "rejected"

    def test_timed_out_routes_to_audit(self):
        c = _make_contract(approval_state=ApprovalState.TIMED_OUT)
        assert route_from_human_review(c) == "timed_out"

    def test_modified_routes_to_analysis(self):
        c = _make_contract(approval_state=ApprovalState.MODIFIED)
        assert route_from_human_review(c) == "modified"

    def test_request_prerequisite_routes_to_audit(self):
        # REQUEST_PREREQUISITE maps to AS.REJECTED in workflow_store,
        # so by the time the guard runs, approval_state is REJECTED.
        c = _make_contract(approval_state=ApprovalState.REJECTED)
        assert route_from_human_review(c) in ("rejected", "timed_out", "audit")

    def test_pending_falls_back_to_rejected(self):
        # PENDING should not reach HUMAN_REVIEW routing, but safe fallback is "rejected"
        c = _make_contract(approval_state=ApprovalState.PENDING)
        assert route_from_human_review(c) == "rejected"


# ── Boolean guard helpers ─────────────────────────────────────────────────────

class TestBooleanGuards:
    def test_human_approved(self):
        assert human_approved(_make_contract(approval_state=ApprovalState.APPROVED))
        assert not human_approved(_make_contract(approval_state=ApprovalState.REJECTED))

    def test_human_rejected_includes_auto_reject_and_timeout(self):
        for state in (ApprovalState.REJECTED, ApprovalState.AUTO_REJECTED, ApprovalState.TIMED_OUT):
            assert human_rejected(_make_contract(approval_state=state))
        assert not human_rejected(_make_contract(approval_state=ApprovalState.APPROVED))

    def test_human_modified(self):
        assert human_modified(_make_contract(approval_state=ApprovalState.MODIFIED))
        assert not human_modified(_make_contract(approval_state=ApprovalState.APPROVED))

    def test_timed_out(self):
        assert timed_out(_make_contract(approval_state=ApprovalState.TIMED_OUT))
        assert not timed_out(_make_contract(approval_state=ApprovalState.REJECTED))

    def test_should_auto_execute(self):
        c = _make_contract(risk_tier=RiskTier.AUTO)
        assert should_auto_execute(c)

    def test_should_auto_execute_blocked_by_reject_flag(self):
        c = _make_contract(risk_tier=RiskTier.AUTO, auto_reject_triggered=True)
        assert not should_auto_execute(c)

    def test_should_auto_reject(self):
        assert should_auto_reject(_make_contract(auto_reject_triggered=True))
        assert not should_auto_reject(_make_contract(auto_reject_triggered=False))

    def test_requires_full_contract_by_reversibility(self):
        perm = _make_contract(reversibility=ReversibilityClass.PERMANENT)
        rev = _make_contract(reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED)
        assert requires_full_contract_by_reversibility(perm)
        assert not requires_full_contract_by_reversibility(rev)

    def test_requires_full_contract_by_history(self):
        c = _make_contract(
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_1847",
                    intent_summary="DELETE users",
                    tables=["users"],
                    outcome="REJECTED",
                    decision_reason="caused data loss",
                    similarity_score=0.9,
                    jaccard_score=0.8,
                    rerank_score=1.0,
                )
            ]
        )
        assert requires_full_contract_by_history(c)

    def test_requires_full_contract_by_history_no_rejection(self):
        c = _make_contract(
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_2000",
                    intent_summary="UPDATE users",
                    tables=["users"],
                    outcome="APPROVED",
                    decision_reason="safe operation",
                    similarity_score=0.6,
                    jaccard_score=0.5,
                    rerank_score=0.1,
                )
            ]
        )
        assert not requires_full_contract_by_history(c)

    def test_sandbox_retry_needed_within_limit(self):
        c = _make_contract(simulation_timeout=True)
        assert sandbox_retry_needed(c, attempt=0)
        assert sandbox_retry_needed(c, attempt=1)

    def test_sandbox_retry_not_needed_at_limit(self):
        c = _make_contract(simulation_timeout=True)
        assert not sandbox_retry_needed(c, attempt=2)

    def test_sandbox_retry_not_needed_without_timeout(self):
        c = _make_contract(simulation_timeout=False)
        assert not sandbox_retry_needed(c, attempt=0)
