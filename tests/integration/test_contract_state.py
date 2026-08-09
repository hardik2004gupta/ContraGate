"""
Tests for CONTRACT state risk tier classification and rollback derivation.

Updated in Phase 5: these tests now import from the canonical Phase 5 modules:
  - agents.contract.risk_rules (classify_risk, _compute_risk_score)
  - agents.contract.contract_builder (derive_rollback_plan)

The orchestrator/states/contract.py no longer contains private helper functions —
it delegates entirely to ContractAgent. The logic under test lives in the modules
above, which are the authoritative implementations.
"""

import pytest

from agents.contract.contract_builder import derive_rollback_plan
from agents.contract.risk_rules import (
    classify_risk,
    _compute_risk_score,
)
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


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_test1234",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="SELECT 1",
        operation_type=OperationType.SELECT,
        reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        # row_confidence=0.8 = fresh statistics baseline (CLAUDE.md §21).
        # Default is 0.0 which triggers the LOW_CONFIDENCE FULL tier rule.
        row_confidence=0.8,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


class TestRiskTierClassification:
    def test_select_low_cost_is_auto(self):
        # SELECT, no rows/cascade/violations → computed score = 0.05 → AUTO
        c = _make_contract(operation_type=OperationType.SELECT)
        tier, _ = classify_risk(c)
        assert tier == RiskTier.AUTO

    def test_permanent_reversibility_forces_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            reversibility=ReversibilityClass.PERMANENT,
        )
        tier, _ = classify_risk(c)
        assert tier == RiskTier.FULL_CONTRACT

    def test_historical_rejection_forces_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.UPDATE,
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
        tier, _ = classify_risk(c)
        assert tier == RiskTier.FULL_CONTRACT

    def test_high_risk_score_forces_full_contract(self):
        # DELETE 2M rows (+0.30) + 20K cascade (+0.10) → 0.35+0.30+0.10 = 0.75 > 0.7
        # Cascade < CASCADE_ROW_THRESHOLD so condition 5 doesn't trigger; tier from score.
        c = _make_contract(
            operation_type=OperationType.DELETE,
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
            estimated_primary_rows=2_000_000,
            cascade=[
                CascadeEntry(table="orders", estimated_rows=20_000,
                             cascade_action="CASCADE", depth=1),
            ],
        )
        tier, score = classify_risk(c)
        assert score > 0.7
        assert tier == RiskTier.FULL_CONTRACT

    def test_external_triggers_below_threshold_standard(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            external_triggers=[
                ExternalTrigger(
                    trigger_name="t1", event="DELETE", extension="pg_net"
                )
            ],
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
        )
        # 1 trigger < threshold of 5 → STANDARD (not FULL)
        tier, _ = classify_risk(c)
        assert tier == RiskTier.STANDARD

    def test_five_external_triggers_forces_full_contract(self):
        triggers = [
            ExternalTrigger(trigger_name=f"t{i}", event="DELETE", extension="pg_net")
            for i in range(5)
        ]
        c = _make_contract(
            operation_type=OperationType.DELETE,
            external_triggers=triggers,
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
        )
        tier, _ = classify_risk(c)
        assert tier == RiskTier.FULL_CONTRACT

    def test_large_cascade_forces_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
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
        tier, _ = classify_risk(c)
        assert tier == RiskTier.FULL_CONTRACT

    def test_prompt_injection_risk_forces_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            prompt_injection_risk=True,
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        )
        tier, _ = classify_risk(c)
        assert tier == RiskTier.FULL_CONTRACT

    def test_policy_required_full_contract(self):
        c = _make_contract(
            operation_type=OperationType.UPDATE,
            required_tier_from_policy="FULL_CONTRACT",
        )
        tier, _ = classify_risk(c)
        assert tier == RiskTier.FULL_CONTRACT

    def test_reversible_write_is_standard(self):
        c = _make_contract(
            operation_type=OperationType.UPDATE,
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
        )
        tier, _ = classify_risk(c)
        assert tier == RiskTier.STANDARD

    def test_select_with_violation_not_auto(self):
        c = _make_contract(
            operation_type=OperationType.SELECT,
            policy_violations=[
                PolicyViolation(
                    rule_id="POLICY_PII_STANDARD_REVIEW",
                    rule_name="PII table",
                    severity="SOFT",
                    description="PII involved",
                )
            ],
        )
        # violations present → AUTO condition fails → STANDARD
        tier, _ = classify_risk(c)
        assert tier != RiskTier.AUTO

    def test_select_with_violations_pushes_to_standard(self):
        # SELECT with 3 violations: 0.05 + 0.30 = 0.35 → STANDARD (not AUTO)
        violations = [
            PolicyViolation(
                rule_id=f"POLICY_{i}",
                rule_name=f"Rule {i}",
                severity="SOFT",
                description=f"Policy {i}",
            )
            for i in range(3)
        ]
        c = _make_contract(
            operation_type=OperationType.SELECT,
            policy_violations=violations,
        )
        tier, score = classify_risk(c)
        assert score >= 0.3
        assert tier != RiskTier.AUTO


class TestRollbackPlanDerivation:
    def test_permanent_no_rollback(self):
        c = _make_contract(reversibility=ReversibilityClass.PERMANENT)
        plan = derive_rollback_plan(c)
        assert plan is None

    def test_reversible_automated_delete_has_plan(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            primary_table="users",
            condition="last_active < NOW() - INTERVAL '2 years'",
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        )
        plan = derive_rollback_plan(c)
        assert plan is not None
        assert "users" in plan

    def test_reversible_pitr_has_pitr_plan(self):
        c = _make_contract(reversibility=ReversibilityClass.REVERSIBLE_PITR)
        plan = derive_rollback_plan(c)
        assert plan is not None
        assert "point-in-time" in plan.lower() or "pitr" in plan.lower()

    def test_partial_reversibility_plan(self):
        c = _make_contract(reversibility=ReversibilityClass.PARTIAL)
        plan = derive_rollback_plan(c)
        assert plan is not None
        assert "partial" in plan.lower()

    def test_insert_rollback_contains_delete(self):
        c = _make_contract(
            operation_type=OperationType.INSERT,
            primary_table="users",
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        )
        plan = derive_rollback_plan(c)
        assert plan is not None
        assert "DELETE" in plan


class TestRiskScoreComputation:
    def test_cascade_increases_score(self):
        base_c = _make_contract()  # SELECT, no cascade
        cascade_c = _make_contract(
            cascade=[
                CascadeEntry(
                    table="invoices",
                    estimated_rows=200_000,
                    cascade_action="CASCADE",
                    depth=1,
                )
            ],
        )
        base_score = _compute_risk_score(base_c)
        cascade_score = _compute_risk_score(cascade_c)
        assert cascade_score > base_score

    def test_rejection_history_increases_score(self):
        base_c = _make_contract()
        hist_c = _make_contract(
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
        assert _compute_risk_score(hist_c) > _compute_risk_score(base_c)

    def test_score_capped_at_one(self):
        c = _make_contract(
            cascade=[
                CascadeEntry(
                    table="t", estimated_rows=1_000_000, cascade_action="CASCADE", depth=1
                )
            ],
        )
        assert _compute_risk_score(c) <= 1.0
