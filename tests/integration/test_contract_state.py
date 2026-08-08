"""
Tests for CONTRACT state: deterministic risk tier classification.

Verifies that the risk tier rules are correctly applied (CLAUDE.md §13).
No LLM involved. No external dependencies.
"""

import pytest

from orchestrator.handoff_schema import (
    CascadeEntry,
    ExternalTrigger,
    HandoffContract,
    HistoricalOperation,
    OperationType,
    PolicyViolation,
    ReversibilityClass,
    RiskTier,
    SourceType,
)
from orchestrator.states.contract import (
    _classify_risk_tier,
    _compute_final_risk_score,
    _derive_rollback_plan,
)


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_test1234",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="SELECT 1",
        operation_type=OperationType.SELECT,
        reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        risk_score=0.1,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


class TestRiskTierClassification:
    def test_select_low_cost_is_auto(self):
        c = _make_contract(operation_type=OperationType.SELECT, risk_score=0.05)
        assert _classify_risk_tier(c) == RiskTier.AUTO

    def test_permanent_reversibility_forces_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            reversibility=ReversibilityClass.PERMANENT,
            risk_score=0.2,
        )
        assert _classify_risk_tier(c) == RiskTier.FULL_CONTRACT

    def test_historical_rejection_forces_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.UPDATE,
            risk_score=0.3,
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_1847",
                    intent_summary="DELETE inactive users",
                    tables=["users"],
                    outcome="REJECTED",
                    decision_reason="Cascade caused loss of billing records",
                    similarity_score=0.9,
                    jaccard_score=0.8,
                    rerank_score=1.0,
                )
            ],
        )
        assert _classify_risk_tier(c) == RiskTier.FULL_CONTRACT

    def test_high_risk_score_forces_full_contract(self):
        c = _make_contract(risk_score=0.8)
        assert _classify_risk_tier(c) == RiskTier.FULL_CONTRACT

    def test_external_triggers_below_threshold_standard(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            risk_score=0.4,
            external_triggers=[
                ExternalTrigger(
                    trigger_name="t1", event="DELETE", extension="pg_net"
                )
            ],
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
        )
        # 1 trigger < threshold of 5 → STANDARD (not FULL)
        assert _classify_risk_tier(c) == RiskTier.STANDARD

    def test_five_external_triggers_forces_full_contract(self):
        triggers = [
            ExternalTrigger(trigger_name=f"t{i}", event="DELETE", extension="pg_net")
            for i in range(5)
        ]
        c = _make_contract(
            operation_type=OperationType.DELETE,
            risk_score=0.4,
            external_triggers=triggers,
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
        )
        assert _classify_risk_tier(c) == RiskTier.FULL_CONTRACT

    def test_large_cascade_forces_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            risk_score=0.5,
            cascade=[
                CascadeEntry(
                    table="invoices",
                    estimated_rows=60_000,
                    cascade_action="CASCADE",
                    depth=1,
                )
            ],
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
        )
        assert _classify_risk_tier(c) == RiskTier.FULL_CONTRACT

    def test_prompt_injection_risk_forces_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            risk_score=0.3,
            prompt_injection_risk=True,
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        )
        assert _classify_risk_tier(c) == RiskTier.FULL_CONTRACT

    def test_policy_required_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.UPDATE,
            risk_score=0.2,
            required_tier_from_policy="FULL_CONTRACT",
        )
        assert _classify_risk_tier(c) == RiskTier.FULL_CONTRACT

    def test_reversible_write_is_standard(self):
        c = _make_contract(
            operation_type=OperationType.UPDATE,
            risk_score=0.4,
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
        )
        assert _classify_risk_tier(c) == RiskTier.STANDARD

    def test_select_with_violation_not_auto(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            risk_score=0.1,
            policy_violations=[
                PolicyViolation(
                    rule_id="POLICY_PII_STANDARD_REVIEW",
                    rule_name="PII table",
                    severity="SOFT",
                    description="PII involved",
                )
            ],
        )
        # violations present → not auto
        assert _classify_risk_tier(c) != RiskTier.AUTO

    def test_select_high_risk_score_not_auto(self):
        c = _make_contract(operation_type=OperationType.SELECT, risk_score=0.5)
        assert _classify_risk_tier(c) != RiskTier.AUTO


class TestRollbackPlanDerivation:
    def test_permanent_no_rollback(self):
        c = _make_contract(reversibility=ReversibilityClass.PERMANENT)
        plan = _derive_rollback_plan(c)
        assert plan is None

    def test_reversible_automated_delete_has_plan(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            primary_table="users",
            condition="last_active < NOW() - INTERVAL '2 years'",
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        )
        plan = _derive_rollback_plan(c)
        assert plan is not None
        assert "users" in plan

    def test_reversible_pitr_has_pitr_plan(self):
        c = _make_contract(reversibility=ReversibilityClass.REVERSIBLE_PITR)
        plan = _derive_rollback_plan(c)
        assert plan is not None
        assert "point-in-time" in plan.lower() or "pitr" in plan.lower()

    def test_partial_reversibility_plan(self):
        c = _make_contract(reversibility=ReversibilityClass.PARTIAL)
        plan = _derive_rollback_plan(c)
        assert plan is not None
        assert "partial" in plan.lower()

    def test_insert_rollback_is_delete(self):
        c = _make_contract(
            operation_type=OperationType.INSERT,
            primary_table="users",
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        )
        plan = _derive_rollback_plan(c)
        assert plan is not None
        assert "DELETE" in plan


class TestRiskScoreComputation:
    def test_cascade_increases_score(self):
        base_c = _make_contract(risk_score=0.3)
        cascade_c = _make_contract(
            risk_score=0.3,
            cascade=[
                CascadeEntry(
                    table="invoices",
                    estimated_rows=200_000,
                    cascade_action="CASCADE",
                    depth=1,
                )
            ],
        )
        base_score = _compute_final_risk_score(base_c)
        cascade_score = _compute_final_risk_score(cascade_c)
        assert cascade_score > base_score

    def test_rejection_history_increases_score(self):
        base_c = _make_contract(risk_score=0.3)
        hist_c = _make_contract(
            risk_score=0.3,
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_1847",
                    intent_summary="DELETE users",
                    tables=["users"],
                    outcome="REJECTED",
                    decision_reason="Caused data loss",
                    similarity_score=0.9,
                    jaccard_score=0.8,
                    rerank_score=1.0,
                )
            ],
        )
        assert _compute_final_risk_score(hist_c) > _compute_final_risk_score(base_c)

    def test_score_capped_at_one(self):
        c = _make_contract(
            risk_score=0.9,
            cascade=[
                CascadeEntry(
                    table="t", estimated_rows=1_000_000, cascade_action="CASCADE", depth=1
                )
            ],
        )
        assert _compute_final_risk_score(c) <= 1.0
