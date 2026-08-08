"""
Unit tests for the deterministic policy rule engine.

All tests run without database access. The rule_engine.evaluate_operation()
function is a pure function — same inputs always produce the same outputs.

Coverage:
  - All 8 policy rules (POLICY_DDL_NO_BACKUP through POLICY_PII_EXPENSIVE_READ)
  - Hard vs soft violation handling
  - required_tier propagation
  - Multiple rules triggered simultaneously
  - No rules triggered (clean operation)
"""
from __future__ import annotations

import pytest

from mcp_servers.policy_store.rule_engine import evaluate_operation


# ── Test fixtures ─────────────────────────────────────────────────────────────

_PII_TABLES = {"users", "invoices"}

_DEFAULT_RULES = [
    {"rule_id": "POLICY_DDL_NO_BACKUP", "rule_name": "DDL No Backup", "severity": "HARD", "action": "AUTO_REJECT", "description": "DDL without confirmed backup", "condition": {}},
    {"rule_id": "POLICY_PII_STANDARD_REVIEW", "rule_name": "PII Standard Review", "severity": "SOFT", "action": "REQUIRE_STANDARD_REVIEW", "description": "PII table operation", "condition": {}},
    {"rule_id": "POLICY_EXTERNAL_INPUT", "rule_name": "External Input", "severity": "SOFT", "action": "REQUIRE_FULL_CONTRACT", "description": "Operation from external user input", "condition": {}},
    {"rule_id": "POLICY_BULK_DELETE_SENSITIVE", "rule_name": "Bulk Delete Sensitive", "severity": "SOFT", "action": "REQUIRE_FULL_CONTRACT", "description": "Bulk delete/update on sensitive tables", "condition": {}},
    {"rule_id": "POLICY_PAYMENT_WEBHOOK", "rule_name": "Payment Webhook", "severity": "SOFT", "action": "REQUIRE_FULL_CONTRACT", "description": "External trigger to payment processor", "condition": {}},
    {"rule_id": "POLICY_AFTER_HOURS", "rule_name": "After Hours", "severity": "SOFT", "action": "QUEUE", "description": "Submission outside business hours", "condition": {"business_hours_start": 7, "business_hours_end": 20}},
    {"rule_id": "POLICY_AUTO_REJECT_PATTERN", "rule_name": "Auto Reject Pattern", "severity": "HARD", "action": "AUTO_REJECT", "description": "Matches previously rejected pattern", "condition": {}},
    {"rule_id": "POLICY_PII_EXPENSIVE_READ", "rule_name": "PII Expensive Read", "severity": "SOFT", "action": "REQUIRE_STANDARD_REVIEW", "description": "Expensive read on PII table", "condition": {}},
]


def _run(
    *,
    operation_type: str = "SELECT",
    tables: list[str] | None = None,
    estimated_rows: int = 0,
    source_type: str = "internal_system",
    has_external_triggers: bool = False,
    submission_hour: int = 10,
    explain_cost: float = 0.0,
    pii_tables: set[str] | None = None,
    rules: list[dict] | None = None,
) -> dict:
    return evaluate_operation(
        operation_type=operation_type,
        tables=tables or [],
        estimated_rows=estimated_rows,
        source_type=source_type,
        has_external_triggers=has_external_triggers,
        submission_hour=submission_hour,
        explain_cost=explain_cost,
        pii_tables=pii_tables if pii_tables is not None else _PII_TABLES,
        all_rules=rules if rules is not None else _DEFAULT_RULES,
    )


# ── Clean operation ───────────────────────────────────────────────────────────

class TestNoRulesTriggered:
    def test_simple_select_on_non_pii_table(self):
        result = _run(operation_type="SELECT", tables=["orders"], submission_hour=10)
        assert result["triggered_rules"] == []
        assert result["has_hard_violation"] is False
        assert result["has_soft_violation"] is False
        assert result["required_tier"] is None
        assert result["auto_reject_reason"] is None

    def test_small_delete_non_sensitive(self):
        result = _run(operation_type="DELETE", tables=["logs"], estimated_rows=50)
        assert result["triggered_rules"] == []


# ── POLICY_DDL_NO_BACKUP ─────────────────────────────────────────────────────

class TestDdlNoBackup:
    def test_drop_triggers_hard_violation(self):
        result = _run(operation_type="DROP", tables=["users"])
        assert result["has_hard_violation"] is True
        assert any(r["rule_id"] == "POLICY_DDL_NO_BACKUP" for r in result["triggered_rules"])

    def test_truncate_triggers_hard_violation(self):
        result = _run(operation_type="TRUNCATE", tables=["orders"])
        assert result["has_hard_violation"] is True

    def test_delete_does_not_trigger(self):
        result = _run(operation_type="DELETE", tables=["logs"])
        assert not any(r["rule_id"] == "POLICY_DDL_NO_BACKUP" for r in result["triggered_rules"])


# ── POLICY_PII_STANDARD_REVIEW ───────────────────────────────────────────────

class TestPiiStandardReview:
    def test_pii_table_triggers(self):
        result = _run(operation_type="SELECT", tables=["users"])
        assert any(r["rule_id"] == "POLICY_PII_STANDARD_REVIEW" for r in result["triggered_rules"])
        assert result["has_soft_violation"] is True

    def test_non_pii_table_does_not_trigger(self):
        result = _run(operation_type="SELECT", tables=["orders"])
        # orders is not in _PII_TABLES
        assert not any(r["rule_id"] == "POLICY_PII_STANDARD_REVIEW" for r in result["triggered_rules"])

    def test_mixed_tables_triggers(self):
        result = _run(operation_type="SELECT", tables=["orders", "users"])
        assert any(r["rule_id"] == "POLICY_PII_STANDARD_REVIEW" for r in result["triggered_rules"])


# ── POLICY_EXTERNAL_INPUT ─────────────────────────────────────────────────────

class TestExternalInput:
    def test_external_user_input_triggers(self):
        result = _run(source_type="external_user_input")
        assert any(r["rule_id"] == "POLICY_EXTERNAL_INPUT" for r in result["triggered_rules"])
        assert result["required_tier"] == "FULL_CONTRACT"

    def test_internal_system_does_not_trigger(self):
        result = _run(source_type="internal_system")
        assert not any(r["rule_id"] == "POLICY_EXTERNAL_INPUT" for r in result["triggered_rules"])

    def test_external_agent_does_not_trigger(self):
        result = _run(source_type="external_agent")
        assert not any(r["rule_id"] == "POLICY_EXTERNAL_INPUT" for r in result["triggered_rules"])


# ── POLICY_BULK_DELETE_SENSITIVE ──────────────────────────────────────────────

class TestBulkDeleteSensitive:
    def test_bulk_delete_users_triggers(self):
        result = _run(operation_type="DELETE", tables=["users"], estimated_rows=50_000)
        assert any(r["rule_id"] == "POLICY_BULK_DELETE_SENSITIVE" for r in result["triggered_rules"])
        assert result["required_tier"] == "FULL_CONTRACT"

    def test_bulk_update_orders_triggers(self):
        result = _run(operation_type="UPDATE", tables=["orders"], estimated_rows=15_000)
        assert any(r["rule_id"] == "POLICY_BULK_DELETE_SENSITIVE" for r in result["triggered_rules"])

    def test_below_threshold_does_not_trigger(self):
        result = _run(operation_type="DELETE", tables=["users"], estimated_rows=9_999)
        assert not any(r["rule_id"] == "POLICY_BULK_DELETE_SENSITIVE" for r in result["triggered_rules"])

    def test_exact_threshold_does_not_trigger(self):
        # > 10,000 required; 10,000 exactly does NOT trigger
        result = _run(operation_type="DELETE", tables=["users"], estimated_rows=10_000)
        assert not any(r["rule_id"] == "POLICY_BULK_DELETE_SENSITIVE" for r in result["triggered_rules"])

    def test_select_does_not_trigger(self):
        result = _run(operation_type="SELECT", tables=["users"], estimated_rows=100_000)
        assert not any(r["rule_id"] == "POLICY_BULK_DELETE_SENSITIVE" for r in result["triggered_rules"])

    def test_non_sensitive_table_does_not_trigger(self):
        result = _run(operation_type="DELETE", tables=["logs"], estimated_rows=100_000)
        assert not any(r["rule_id"] == "POLICY_BULK_DELETE_SENSITIVE" for r in result["triggered_rules"])


# ── POLICY_PAYMENT_WEBHOOK ────────────────────────────────────────────────────

class TestPaymentWebhook:
    def test_external_trigger_triggers(self):
        result = _run(has_external_triggers=True)
        assert any(r["rule_id"] == "POLICY_PAYMENT_WEBHOOK" for r in result["triggered_rules"])
        assert result["required_tier"] == "FULL_CONTRACT"

    def test_no_external_trigger_does_not_trigger(self):
        result = _run(has_external_triggers=False)
        assert not any(r["rule_id"] == "POLICY_PAYMENT_WEBHOOK" for r in result["triggered_rules"])


# ── POLICY_AFTER_HOURS ────────────────────────────────────────────────────────

class TestAfterHours:
    def test_before_business_hours_triggers(self):
        result = _run(submission_hour=6)
        assert any(r["rule_id"] == "POLICY_AFTER_HOURS" for r in result["triggered_rules"])

    def test_after_business_hours_triggers(self):
        result = _run(submission_hour=21)
        assert any(r["rule_id"] == "POLICY_AFTER_HOURS" for r in result["triggered_rules"])

    def test_during_business_hours_does_not_trigger(self):
        for hour in [7, 10, 14, 19]:
            result = _run(submission_hour=hour)
            assert not any(r["rule_id"] == "POLICY_AFTER_HOURS" for r in result["triggered_rules"]), (
                f"POLICY_AFTER_HOURS should not trigger at hour {hour}"
            )

    def test_exactly_at_end_hour_triggers(self):
        # Business hours are [7, 20) — hour 20 is outside
        result = _run(submission_hour=20)
        assert any(r["rule_id"] == "POLICY_AFTER_HOURS" for r in result["triggered_rules"])


# ── POLICY_PII_EXPENSIVE_READ ─────────────────────────────────────────────────

class TestPiiExpensiveRead:
    def test_expensive_select_on_pii_triggers(self):
        result = _run(
            operation_type="SELECT",
            tables=["users"],
            explain_cost=150_000.0,
        )
        assert any(r["rule_id"] == "POLICY_PII_EXPENSIVE_READ" for r in result["triggered_rules"])

    def test_below_threshold_does_not_trigger(self):
        result = _run(
            operation_type="SELECT",
            tables=["users"],
            explain_cost=99_999.0,
        )
        assert not any(r["rule_id"] == "POLICY_PII_EXPENSIVE_READ" for r in result["triggered_rules"])

    def test_write_on_pii_does_not_trigger(self):
        result = _run(
            operation_type="DELETE",
            tables=["users"],
            explain_cost=200_000.0,
        )
        assert not any(r["rule_id"] == "POLICY_PII_EXPENSIVE_READ" for r in result["triggered_rules"])

    def test_expensive_read_on_non_pii_does_not_trigger(self):
        result = _run(
            operation_type="SELECT",
            tables=["orders"],
            explain_cost=200_000.0,
        )
        assert not any(r["rule_id"] == "POLICY_PII_EXPENSIVE_READ" for r in result["triggered_rules"])


# ── Multiple rules ────────────────────────────────────────────────────────────

class TestMultipleRules:
    def test_pii_and_external_input_both_trigger(self):
        result = _run(
            operation_type="SELECT",
            tables=["users"],
            source_type="external_user_input",
        )
        rule_ids = {r["rule_id"] for r in result["triggered_rules"]}
        assert "POLICY_PII_STANDARD_REVIEW" in rule_ids
        assert "POLICY_EXTERNAL_INPUT" in rule_ids
        assert result["required_tier"] == "FULL_CONTRACT"

    def test_hard_violation_wins_over_soft(self):
        result = _run(
            operation_type="DROP",
            tables=["users"],
            source_type="external_user_input",
        )
        assert result["has_hard_violation"] is True
        assert result["has_soft_violation"] is True
        assert result["auto_reject_reason"] is not None

    def test_empty_rules_list_returns_clean(self):
        result = _run(
            operation_type="DELETE",
            tables=["users"],
            estimated_rows=100_000,
            rules=[],
        )
        assert result["triggered_rules"] == []
        assert result["has_hard_violation"] is False


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_inputs_same_output(self):
        kwargs = dict(
            operation_type="DELETE",
            tables=["users"],
            estimated_rows=50_000,
            source_type="internal_system",
            has_external_triggers=False,
            submission_hour=10,
            explain_cost=0.0,
        )
        result1 = _run(**kwargs)
        result2 = _run(**kwargs)
        assert result1 == result2
