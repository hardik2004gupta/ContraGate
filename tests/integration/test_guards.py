"""
Tests for guard condition functions.

Guards are pure deterministic functions — no external dependencies.
They take HandoffContract and return routing keys.
"""

import pytest

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    HistoricalOperation,
    OperationType,
    PolicyViolation,
    ReversibilityClass,
    RiskTier,
    SourceType,
)
from orchestrator.guards import (
    _is_fast_path,
    requires_full_contract_by_history,
    requires_full_contract_by_reversibility,
    route_from_human_review,
    route_from_risk_gate,
)


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_test1234",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="SELECT 1",
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


class TestRouteFromRiskGate:
    def test_hard_violation_routes_to_auto_reject(self):
        c = _make_contract(auto_reject_triggered=True)
        assert route_from_risk_gate(c) == "auto_reject"

    def test_low_risk_select_routes_to_auto_execute(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            risk_score=0.05,
        )
        assert route_from_risk_gate(c) == "auto_execute"

    def test_write_operation_routes_to_full_pipeline(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            risk_score=0.6,
        )
        assert route_from_risk_gate(c) == "full_pipeline"

    def test_select_with_high_risk_routes_to_full_pipeline(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            risk_score=0.8,
        )
        assert route_from_risk_gate(c) == "full_pipeline"

    def test_select_with_policy_violation_routes_to_full_pipeline(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            risk_score=0.05,
            policy_violations=[
                PolicyViolation(
                    rule_id="POLICY_PII_STANDARD_REVIEW",
                    rule_name="PII",
                    severity="SOFT",
                    description="PII table",
                )
            ],
        )
        assert route_from_risk_gate(c) == "full_pipeline"

    def test_select_with_required_tier_routes_to_full_pipeline(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            risk_score=0.05,
            required_tier_from_policy="FULL_CONTRACT",
        )
        assert route_from_risk_gate(c) == "full_pipeline"


class TestIsFastPath:
    def test_clean_select_is_fast_path(self):
        c = _make_contract(operation_type=OperationType.SELECT, risk_score=0.05)
        assert _is_fast_path(c) is True

    def test_delete_is_not_fast_path(self):
        c = _make_contract(operation_type=OperationType.DELETE, risk_score=0.05)
        assert _is_fast_path(c) is False

    def test_select_with_violation_not_fast_path(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            risk_score=0.05,
            policy_violations=[
                PolicyViolation(
                    rule_id="X", rule_name="X", severity="SOFT", description="x"
                )
            ],
        )
        assert _is_fast_path(c) is False

    def test_select_with_high_risk_score_not_fast_path(self):
        c = _make_contract(operation_type=OperationType.SELECT, risk_score=0.5)
        assert _is_fast_path(c) is False


class TestRouteFromHumanReview:
    def test_approved_routes_to_approved(self):
        c = _make_contract(
            approval_state=ApprovalState.APPROVED,
            human_decision="APPROVE",
            decision_reason="Reviewed carefully and approved this operation",
        )
        assert route_from_human_review(c) == "approved"

    def test_rejected_routes_to_rejected(self):
        c = _make_contract(
            approval_state=ApprovalState.REJECTED,
            human_decision="REJECT",
            decision_reason="Too many cascade deletions involved here",
        )
        assert route_from_human_review(c) == "rejected"

    def test_timed_out_routes_to_timed_out(self):
        c = _make_contract(approval_state=ApprovalState.TIMED_OUT)
        assert route_from_human_review(c) == "timed_out"

    def test_modified_routes_to_modified(self):
        c = _make_contract(approval_state=ApprovalState.MODIFIED)
        assert route_from_human_review(c) == "modified"

    def test_pending_routes_to_rejected_as_fallback(self):
        c = _make_contract(approval_state=ApprovalState.PENDING)
        assert route_from_human_review(c) == "rejected"


class TestFullContractGuards:
    def test_permanent_reversibility_forces_full_contract(self):
        c = _make_contract(reversibility=ReversibilityClass.PERMANENT)
        assert requires_full_contract_by_reversibility(c) is True

    def test_reversible_automated_does_not_force_full_contract(self):
        c = _make_contract(reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED)
        assert requires_full_contract_by_reversibility(c) is False

    def test_historical_rejection_forces_full_contract(self):
        c = _make_contract(
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_1847",
                    intent_summary="DELETE inactive users",
                    tables=["users"],
                    outcome="REJECTED",
                    decision_reason="Cascade into invoices caused loss of billing records",
                    similarity_score=0.91,
                    jaccard_score=0.75,
                    rerank_score=1.0,
                )
            ]
        )
        assert requires_full_contract_by_history(c) is True

    def test_approved_historical_does_not_force_full_contract(self):
        c = _make_contract(
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_5555",
                    intent_summary="UPDATE small batch",
                    tables=["users"],
                    outcome="APPROVED",
                    decision_reason="Small batch update, safe to approve here",
                    similarity_score=0.7,
                    jaccard_score=0.6,
                    rerank_score=0.07,
                )
            ]
        )
        assert requires_full_contract_by_history(c) is False

    def test_no_historical_no_force(self):
        c = _make_contract()
        assert requires_full_contract_by_history(c) is False
