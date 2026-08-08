"""
Tests for reversibility_rules.py — pure unit tests, no database required.

Verifies the deterministic priority-order classification across all fixture
scenarios. Every reversibility classification path must be exercised.

Priority order (CLAUDE.md §13, tested explicitly):
  1. DDL → PERMANENT (even if soft-delete column present)
  2. Non-transactional trigger + reversible DB change → PARTIAL
  3. Non-transactional trigger + permanent DB change → PERMANENT
  4. DELETE/UPDATE, no soft-delete, no PITR → PERMANENT
  5. Soft-delete column present → REVERSIBLE_AUTOMATED
  6. PITR confirmed → REVERSIBLE_PITR
  7. INSERT → REVERSIBLE_AUTOMATED
  8. SELECT → REVERSIBLE_AUTOMATED
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from sql_analysis_lib.reversibility_rules import (
    ReversibilityClass,
    ReversibilityResult,
    classify_reversibility,
)
from sql_analysis_lib.trigger_detector import TriggerAnalysis, TriggerInfo

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "operations"


# ── Helper factories ─────────────────────────────────────────────────────────

def _no_triggers(table: str = "test_table") -> TriggerAnalysis:
    return TriggerAnalysis(
        table=table,
        triggers=[],
        has_permanent_side_effects=False,
        non_transactional_triggers=[],
    )


def _pg_net_trigger(table: str = "test_table") -> TriggerAnalysis:
    trigger = TriggerInfo(
        trigger_name="after_delete_notify",
        table_name=table,
        event="DELETE",
        timing="AFTER",
        function_name="notify_external",
        invokes_non_transactional=True,
        non_transactional_extension="pg_net",
        estimated_external_calls=None,
        target_endpoint="https://hooks.internal/event",
    )
    return TriggerAnalysis(
        table=table,
        triggers=[trigger],
        has_permanent_side_effects=True,
        non_transactional_triggers=[trigger],
    )


def _dblink_trigger(table: str = "test_table") -> TriggerAnalysis:
    trigger = TriggerInfo(
        trigger_name="after_delete_sync",
        table_name=table,
        event="DELETE",
        timing="AFTER",
        function_name="sync_remote",
        invokes_non_transactional=True,
        non_transactional_extension="dblink",
        estimated_external_calls=None,
        target_endpoint=None,
    )
    return TriggerAnalysis(
        table=table,
        triggers=[trigger],
        has_permanent_side_effects=True,
        non_transactional_triggers=[trigger],
    )


# ── DDL scenarios (Priority 1) ───────────────────────────────────────────────

class TestDDLAlwaysPermanent:
    @pytest.mark.parametrize("op", ["DROP", "TRUNCATE", "ALTER", "CREATE", "DDL",
                                     "drop", "truncate", "alter"])
    def test_ddl_is_permanent(self, op):
        result = classify_reversibility(
            operation_type=op,
            table="any_table",
            has_soft_delete_column=True,  # soft-delete present but DDL wins
            trigger_analysis=_no_triggers(),
            pitr_confirmed=True,           # PITR present but DDL wins
            pitr_window_hours=168.0,
            estimated_rows=1000,
        )
        assert result.classification == ReversibilityClass.PERMANENT

    def test_ddl_soft_delete_does_not_override(self):
        """DDL priority 1 must win over soft-delete presence (CLAUDE.md §13)."""
        result = classify_reversibility(
            operation_type="TRUNCATE",
            table="cg_users",
            has_soft_delete_column=True,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=5000,
        )
        assert result.classification == ReversibilityClass.PERMANENT
        assert "DDL" in result.reason

    def test_ddl_reason_contains_operation_name(self):
        result = classify_reversibility(
            operation_type="DROP",
            table="some_table",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=0,
        )
        assert "DROP" in result.reason or "DDL" in result.reason

    def test_ddl_fixtures(self, fixture_loader=None):
        """Verify all ddl_operations fixtures classify as PERMANENT."""
        path = FIXTURES_DIR / "ddl_operations.json"
        scenarios = json.loads(path.read_text())
        for s in scenarios:
            result = classify_reversibility(
                operation_type=s["inputs"]["operation_type"] if "inputs" in s else s["operation_type"],
                table=s["table"],
                has_soft_delete_column=False,
                trigger_analysis=_no_triggers(s["table"]),
                pitr_confirmed=False,
                pitr_window_hours=None,
                estimated_rows=0,
            )
            assert result.classification == ReversibilityClass.PERMANENT, (
                f"Scenario {s['scenario_id']} expected PERMANENT, got {result.classification}"
            )


# ── Trigger scenarios (Priority 2 & 3) ──────────────────────────────────────

class TestTriggerSideEffects:
    def test_pg_net_trigger_with_reversible_db_is_partial(self):
        """DB reversible + pg_net trigger → PARTIAL (Priority 2)."""
        result = classify_reversibility(
            operation_type="UPDATE",
            table="cg_users",
            has_soft_delete_column=True,
            trigger_analysis=_pg_net_trigger("cg_users"),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=1,
        )
        assert result.classification == ReversibilityClass.PARTIAL

    def test_pg_net_trigger_with_permanent_db_is_permanent(self):
        """DB permanent + pg_net trigger → PERMANENT (Priority 3)."""
        result = classify_reversibility(
            operation_type="DELETE",
            table="cg_orders",
            has_soft_delete_column=False,
            trigger_analysis=_pg_net_trigger("cg_orders"),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=500,
        )
        assert result.classification == ReversibilityClass.PERMANENT

    def test_dblink_trigger_with_pitr_is_partial(self):
        """DB REVERSIBLE_PITR + dblink trigger → PARTIAL."""
        result = classify_reversibility(
            operation_type="DELETE",
            table="cg_orders",
            has_soft_delete_column=False,
            trigger_analysis=_dblink_trigger("cg_orders"),
            pitr_confirmed=True,
            pitr_window_hours=72.0,
            estimated_rows=200,
        )
        assert result.classification == ReversibilityClass.PARTIAL

    def test_permanent_components_includes_trigger_name(self):
        result = classify_reversibility(
            operation_type="UPDATE",
            table="cg_users",
            has_soft_delete_column=True,
            trigger_analysis=_pg_net_trigger("cg_users"),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=1,
        )
        assert any("after_delete_notify" in c for c in result.permanent_components)

    def test_trigger_operations_fixture(self):
        """Verify trigger_operations fixtures match expected reversibility."""
        path = FIXTURES_DIR / "trigger_operations.json"
        scenarios = json.loads(path.read_text())
        for s in scenarios:
            inp = s["inputs"]
            trigger_analysis = _no_triggers(s["table"])
            if inp.get("trigger_invokes_non_transactional"):
                ext = inp.get("trigger_non_transactional_extension", "pg_net")
                trigger = TriggerInfo(
                    trigger_name=inp.get("trigger_name", "test_trigger"),
                    table_name=s["table"],
                    event=inp.get("trigger_event", "DELETE"),
                    timing=inp.get("trigger_timing", "AFTER"),
                    function_name="test_fn",
                    invokes_non_transactional=True,
                    non_transactional_extension=ext,
                    estimated_external_calls=None,
                    target_endpoint=None,
                )
                trigger_analysis = TriggerAnalysis(
                    table=s["table"],
                    triggers=[trigger],
                    has_permanent_side_effects=True,
                    non_transactional_triggers=[trigger],
                )

            result = classify_reversibility(
                operation_type=s["operation_type"],
                table=s["table"],
                has_soft_delete_column=inp["has_soft_delete_column"],
                trigger_analysis=trigger_analysis,
                pitr_confirmed=inp["pitr_confirmed"],
                pitr_window_hours=inp["pitr_window_hours"],
                estimated_rows=inp["estimated_rows"],
            )
            expected = s["expected"]["reversibility"]
            assert result.classification.value == expected, (
                f"Scenario {s['scenario_id']}: expected {expected}, got {result.classification.value}"
            )


# ── DELETE scenarios (Priority 4, 5, 6) ─────────────────────────────────────

class TestDeleteReversibility:
    def test_delete_no_soft_delete_no_pitr_is_permanent(self):
        result = classify_reversibility(
            operation_type="DELETE",
            table="cg_orders",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=1000,
        )
        assert result.classification == ReversibilityClass.PERMANENT

    def test_delete_with_soft_delete_is_automated(self):
        result = classify_reversibility(
            operation_type="DELETE",
            table="cg_users",
            has_soft_delete_column=True,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=1,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_AUTOMATED

    def test_delete_with_pitr_is_pitr(self):
        result = classify_reversibility(
            operation_type="DELETE",
            table="cg_customer_profiles",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=True,
            pitr_window_hours=168.0,
            estimated_rows=1,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_PITR

    def test_soft_delete_wins_over_pitr(self):
        """When both soft-delete and PITR present, REVERSIBLE_AUTOMATED wins."""
        result = classify_reversibility(
            operation_type="DELETE",
            table="cg_users",
            has_soft_delete_column=True,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=True,
            pitr_window_hours=168.0,
            estimated_rows=500,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_AUTOMATED

    def test_bulk_delete_fixtures(self):
        path = FIXTURES_DIR / "bulk_delete.json"
        scenarios = json.loads(path.read_text())
        for s in scenarios:
            inp = s["inputs"]
            result = classify_reversibility(
                operation_type=s["operation_type"],
                table=s["table"],
                has_soft_delete_column=inp["has_soft_delete_column"],
                trigger_analysis=_no_triggers(s["table"]),
                pitr_confirmed=inp["pitr_confirmed"],
                pitr_window_hours=inp["pitr_window_hours"],
                estimated_rows=inp["estimated_rows"],
            )
            expected = s["expected"]["reversibility"]
            assert result.classification.value == expected, (
                f"Scenario {s['scenario_id']}: expected {expected}, got {result.classification.value}"
            )


# ── UPDATE scenarios ──────────────────────────────────────────────────────────

class TestUpdateReversibility:
    def test_update_no_recovery_is_permanent(self):
        result = classify_reversibility(
            operation_type="UPDATE",
            table="cg_employees",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=25000,
        )
        assert result.classification == ReversibilityClass.PERMANENT

    def test_update_with_pitr_is_pitr(self):
        result = classify_reversibility(
            operation_type="UPDATE",
            table="cg_employees",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=True,
            pitr_window_hours=72.0,
            estimated_rows=1,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_PITR

    def test_update_with_soft_delete_is_automated(self):
        result = classify_reversibility(
            operation_type="UPDATE",
            table="cg_users",
            has_soft_delete_column=True,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=1,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_AUTOMATED

    def test_selective_update_fixtures(self):
        path = FIXTURES_DIR / "selective_update.json"
        scenarios = json.loads(path.read_text())
        for s in scenarios:
            inp = s["inputs"]
            result = classify_reversibility(
                operation_type=s["operation_type"],
                table=s["table"],
                has_soft_delete_column=inp["has_soft_delete_column"],
                trigger_analysis=_no_triggers(s["table"]),
                pitr_confirmed=inp["pitr_confirmed"],
                pitr_window_hours=inp["pitr_window_hours"],
                estimated_rows=inp["estimated_rows"],
            )
            expected = s["expected"]["reversibility"]
            assert result.classification.value == expected, (
                f"Scenario {s['scenario_id']}: expected {expected}, got {result.classification.value}"
            )


# ── INSERT and SELECT scenarios ───────────────────────────────────────────────

class TestInsertSelectReversibility:
    def test_insert_is_always_automated(self):
        result = classify_reversibility(
            operation_type="INSERT",
            table="cg_employees",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=1,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_AUTOMATED

    def test_insert_batch_is_automated(self):
        result = classify_reversibility(
            operation_type="INSERT",
            table="cg_notifications",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=5000,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_AUTOMATED

    def test_select_is_always_automated(self):
        result = classify_reversibility(
            operation_type="SELECT",
            table="cg_departments",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=3,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_AUTOMATED

    def test_read_operations_fixtures(self):
        path = FIXTURES_DIR / "read_operations.json"
        scenarios = json.loads(path.read_text())
        for s in scenarios:
            inp = s["inputs"]
            result = classify_reversibility(
                operation_type=s["operation_type"],
                table=s["table"],
                has_soft_delete_column=inp["has_soft_delete_column"],
                trigger_analysis=_no_triggers(s["table"]),
                pitr_confirmed=inp["pitr_confirmed"],
                pitr_window_hours=inp["pitr_window_hours"],
                estimated_rows=inp["estimated_rows"],
            )
            expected = s["expected"]["reversibility"]
            assert result.classification.value == expected, (
                f"Scenario {s['scenario_id']}: expected {expected}, got {result.classification.value}"
            )

    def test_edge_cases_fixtures(self):
        path = FIXTURES_DIR / "edge_cases.json"
        scenarios = json.loads(path.read_text())
        for s in scenarios:
            inp = s["inputs"]
            result = classify_reversibility(
                operation_type=s["operation_type"],
                table=s["table"],
                has_soft_delete_column=inp["has_soft_delete_column"],
                trigger_analysis=_no_triggers(s["table"]),
                pitr_confirmed=inp["pitr_confirmed"],
                pitr_window_hours=inp["pitr_window_hours"],
                estimated_rows=inp["estimated_rows"],
            )
            expected = s["expected"]["reversibility"]
            assert result.classification.value == expected, (
                f"Scenario {s['scenario_id']}: expected {expected}, got {result.classification.value}"
            )


# ── Result structure ──────────────────────────────────────────────────────────

class TestResultStructure:
    def test_automated_recovery_sql_is_always_none(self):
        """ADR-008: No LLM-generated rollback SQL is ever produced."""
        for op in ["DELETE", "UPDATE", "INSERT", "SELECT"]:
            result = classify_reversibility(
                operation_type=op,
                table="t",
                has_soft_delete_column=True,
                trigger_analysis=_no_triggers(),
                pitr_confirmed=True,
                pitr_window_hours=72.0,
                estimated_rows=1,
            )
            assert result.automated_recovery_sql is None, (
                f"Expected automated_recovery_sql=None for {op}, got {result.automated_recovery_sql!r}"
            )

    def test_pitr_fields_propagated(self):
        result = classify_reversibility(
            operation_type="DELETE",
            table="t",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=True,
            pitr_window_hours=96.0,
            estimated_rows=1,
        )
        assert result.pitr_available is True
        assert result.pitr_window_hours == pytest.approx(96.0)

    def test_permanent_components_empty_when_reversible(self):
        result = classify_reversibility(
            operation_type="DELETE",
            table="cg_users",
            has_soft_delete_column=True,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=1,
        )
        assert result.permanent_components == []


# ── Unknown operation type (fail-closed) ─────────────────────────────────────

class TestUnknownOperationType:
    """Unknown operation types must NEVER return REVERSIBLE_AUTOMATED — fail closed."""

    @pytest.mark.parametrize("op", [
        "MERGE", "CALL", "VACUUM", "ANALYZE", "REFRESH",
        "COPY", "EXPLAIN", "GRANT", "REVOKE", "unknown_op",
        "",  # empty string
    ])
    def test_unknown_operation_raises_value_error(self, op):
        with pytest.raises(ValueError, match="[Uu]nknown operation"):
            classify_reversibility(
                operation_type=op,
                table="any_table",
                has_soft_delete_column=False,
                trigger_analysis=_no_triggers(),
                pitr_confirmed=False,
                pitr_window_hours=None,
                estimated_rows=100,
            )

    def test_unknown_op_with_pitr_still_raises(self):
        """PITR does not save an unknown operation from failing closed."""
        with pytest.raises(ValueError):
            classify_reversibility(
                operation_type="MERGE",
                table="any_table",
                has_soft_delete_column=False,
                trigger_analysis=_no_triggers(),
                pitr_confirmed=True,
                pitr_window_hours=72.0,
                estimated_rows=1,
            )

    def test_unknown_op_with_soft_delete_still_raises(self):
        """Soft-delete does not save an unknown operation from failing closed."""
        with pytest.raises(ValueError):
            classify_reversibility(
                operation_type="VACUUM",
                table="any_table",
                has_soft_delete_column=True,
                trigger_analysis=_no_triggers(),
                pitr_confirmed=False,
                pitr_window_hours=None,
                estimated_rows=1,
            )


# ── PITR validation ───────────────────────────────────────────────────────────

class TestPitrValidation:
    """pitr_confirmed=True with invalid pitr_window_hours must raise ValueError."""

    def test_pitr_confirmed_with_none_hours_raises(self):
        with pytest.raises(ValueError, match="pitr_window_hours"):
            classify_reversibility(
                operation_type="DELETE",
                table="t",
                has_soft_delete_column=False,
                trigger_analysis=_no_triggers(),
                pitr_confirmed=True,
                pitr_window_hours=None,
                estimated_rows=1,
            )

    def test_pitr_confirmed_with_zero_hours_raises(self):
        with pytest.raises(ValueError, match="pitr_window_hours"):
            classify_reversibility(
                operation_type="DELETE",
                table="t",
                has_soft_delete_column=False,
                trigger_analysis=_no_triggers(),
                pitr_confirmed=True,
                pitr_window_hours=0,
                estimated_rows=1,
            )

    def test_pitr_confirmed_with_negative_hours_raises(self):
        with pytest.raises(ValueError, match="pitr_window_hours"):
            classify_reversibility(
                operation_type="DELETE",
                table="t",
                has_soft_delete_column=False,
                trigger_analysis=_no_triggers(),
                pitr_confirmed=True,
                pitr_window_hours=-1,
                estimated_rows=1,
            )

    def test_pitr_confirmed_with_valid_hours_succeeds(self):
        """A small positive window is valid."""
        result = classify_reversibility(
            operation_type="DELETE",
            table="t",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=True,
            pitr_window_hours=0.5,
            estimated_rows=1,
        )
        assert result.classification == ReversibilityClass.REVERSIBLE_PITR

    def test_pitr_not_confirmed_with_none_hours_is_ok(self):
        """pitr_confirmed=False with pitr_window_hours=None is the normal case."""
        result = classify_reversibility(
            operation_type="DELETE",
            table="t",
            has_soft_delete_column=False,
            trigger_analysis=_no_triggers(),
            pitr_confirmed=False,
            pitr_window_hours=None,
            estimated_rows=1,
        )
        assert result.classification == ReversibilityClass.PERMANENT
