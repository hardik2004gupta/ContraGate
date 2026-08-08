"""
Unit tests for HandoffContract schema (Pydantic v2).

Tests: field defaults, enum values, provenance tracking, model_validator.
These run without any external dependencies.
"""

import pytest
from datetime import datetime
from pydantic import ValidationError

from orchestrator.handoff_schema import (
    ApprovalState,
    CascadeEntry,
    ExternalTrigger,
    HandoffContract,
    HistoricalOperation,
    OperationType,
    PolicyViolation,
    ProvenanceEntry,
    ReversibilityClass,
    RiskTier,
    SourceType,
)


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_test1234",
        tenant_id="demo_tenant",
        submitted_by="test_agent",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="SELECT 1",
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


class TestHandoffContractDefaults:
    def test_default_approval_state_is_pending(self):
        c = _make_contract()
        assert c.approval_state == ApprovalState.PENDING

    def test_default_operation_type_is_unknown(self):
        c = _make_contract()
        assert c.operation_type == OperationType.UNKNOWN

    def test_default_risk_score_is_zero(self):
        c = _make_contract()
        assert c.risk_score == 0.0

    def test_default_cascade_is_empty(self):
        c = _make_contract()
        assert c.cascade == []

    def test_default_provenance_is_empty(self):
        c = _make_contract()
        assert c.workflow_provenance == []

    def test_submission_timestamp_is_set(self):
        c = _make_contract()
        assert isinstance(c.submission_timestamp, datetime)

    def test_retrieval_available_default_true(self):
        c = _make_contract()
        assert c.retrieval_available is True

    def test_simulation_available_default_true(self):
        c = _make_contract()
        assert c.simulation_available is True


class TestProvenanceTracking:
    def test_add_provenance_appends_entry(self):
        c = _make_contract()
        c.add_provenance(agent="INTAKE", field_written="operation_id")
        assert len(c.workflow_provenance) == 1
        entry = c.workflow_provenance[0]
        assert entry.agent == "INTAKE"
        assert entry.field_written == "operation_id"
        assert entry.llm_involved is False

    def test_add_provenance_multiple_entries(self):
        c = _make_contract()
        c.add_provenance("INTAKE", "operation_id")
        c.add_provenance("RISK_GATE", "policy_violations", llm_involved=False)
        c.add_provenance("ANALYSIS_STUB", "cascade", llm_involved=False)
        assert len(c.workflow_provenance) == 3
        assert c.workflow_provenance[2].agent == "ANALYSIS_STUB"

    def test_provenance_timestamp_set_automatically(self):
        c = _make_contract()
        before = datetime.utcnow()
        c.add_provenance("AUDIT", "audit_record")
        after = datetime.utcnow()
        ts = c.workflow_provenance[0].timestamp
        assert before <= ts <= after


class TestModelValidator:
    def test_approve_with_short_reason_raises(self):
        with pytest.raises(ValidationError):
            HandoffContract(
                operation_id="cg_test5678",
                tenant_id="demo_tenant",
                submitted_by="test",
                source_type=SourceType.INTERNAL_SYSTEM,
                raw_sql="SELECT 1",
                human_decision="APPROVE",
                decision_reason="short",  # < 10 chars
            )

    def test_approve_with_long_reason_valid(self):
        c = _make_contract(
            human_decision="APPROVE",
            decision_reason="This is a valid reason with enough characters",
        )
        assert c.decision_reason == "This is a valid reason with enough characters"

    def test_reject_with_short_reason_raises(self):
        with pytest.raises(ValidationError):
            _make_contract(
                human_decision="REJECT",
                decision_reason="too short",
            )

    def test_none_decision_reason_skips_validation(self):
        c = _make_contract(human_decision="APPROVE", decision_reason=None)
        assert c.decision_reason is None


class TestEmbeddedModels:
    def test_cascade_entry(self):
        entry = CascadeEntry(
            table="orders",
            estimated_rows=1500,
            cascade_action="CASCADE",
            depth=1,
        )
        assert entry.table == "orders"
        assert entry.actual_rows is None

    def test_external_trigger(self):
        trigger = ExternalTrigger(
            trigger_name="send_email_trigger",
            event="DELETE",
            extension="pg_net",
        )
        assert trigger.extension == "pg_net"
        assert trigger.estimated_calls is None

    def test_historical_operation(self):
        hist = HistoricalOperation(
            operation_id="cg_1847",
            intent_summary="DELETE inactive users",
            tables=["users", "orders", "invoices"],
            outcome="REJECTED",
            decision_reason="Cascade into invoices caused loss of 8,200 billing records",
            similarity_score=0.91,
            jaccard_score=0.75,
            rerank_score=1.0,
        )
        assert hist.outcome == "REJECTED"
        assert hist.rerank_score == 1.0

    def test_policy_violation(self):
        v = PolicyViolation(
            rule_id="POLICY_DDL_NO_BACKUP",
            rule_name="DDL without backup",
            severity="HARD",
            description="DROP without confirmed backup",
        )
        assert v.severity == "HARD"


class TestRiskTierEnum:
    def test_auto_tier(self):
        assert RiskTier.AUTO.value == "AUTO"

    def test_standard_tier(self):
        assert RiskTier.STANDARD.value == "STANDARD"

    def test_full_contract_tier(self):
        assert RiskTier.FULL_CONTRACT.value == "FULL_CONTRACT"


class TestReversibilityEnum:
    def test_all_four_values(self):
        expected = {"REVERSIBLE_AUTOMATED", "REVERSIBLE_PITR", "PARTIAL", "PERMANENT"}
        actual = {v.value for v in ReversibilityClass}
        assert actual == expected


class TestModelDump:
    def test_model_dump_json_round_trips(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            primary_table="users",
            estimated_primary_rows=5000,
        )
        c.add_provenance("INTAKE", "operation_id")
        json_str = c.model_dump_json()
        assert "cg_test1234" in json_str
        assert "demo_tenant" in json_str
        assert "INTAKE" in json_str
