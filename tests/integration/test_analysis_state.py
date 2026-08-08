"""
Tests for ANALYSIS state: reversibility classification and SQL parsing.

These tests cover the deterministic code paths in analysis.py that
require no external dependencies (no MCP server calls).
"""

import pytest

from orchestrator.handoff_schema import (
    ExternalTrigger,
    HandoffContract,
    OperationType,
    ReversibilityClass,
    SourceType,
)
from orchestrator.states.analysis import (
    _classify_reversibility,
    _extract_where,
    _identify_permanent_components,
    _parse_sql_intent,
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


class TestParseSqlIntent:
    def test_select_from_table(self):
        table, cond = _parse_sql_intent("SELECT * FROM users WHERE id = 1")
        assert table == "users"
        assert "id = 1" in cond

    def test_delete_from_table(self):
        table, cond = _parse_sql_intent(
            "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        )
        assert table == "users"
        assert "last_active" in cond

    def test_update_table(self):
        table, cond = _parse_sql_intent("UPDATE orders SET status = 'x' WHERE id = 5")
        assert table == "orders"
        assert "id = 5" in cond

    def test_insert_into_table(self):
        table, cond = _parse_sql_intent("INSERT INTO notifications (user_id) VALUES (1)")
        assert table == "notifications"
        assert cond == ""

    def test_truncate_table(self):
        table, _ = _parse_sql_intent("TRUNCATE TABLE orders")
        assert table == "orders"

    def test_drop_table(self):
        table, _ = _parse_sql_intent("DROP TABLE users")
        assert table == "users"

    def test_empty_sql_returns_empty(self):
        table, cond = _parse_sql_intent("")
        assert table == ""
        assert cond == ""


class TestExtractWhere:
    def test_simple_where(self):
        result = _extract_where("DELETE FROM t WHERE status = 'active'")
        assert "status = 'active'" in result

    def test_complex_where(self):
        sql = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years' ORDER BY id"
        result = _extract_where(sql)
        assert "last_active" in result
        # Should not include ORDER BY
        assert "ORDER" not in result

    def test_no_where_returns_empty(self):
        result = _extract_where("DELETE FROM users")
        assert result == ""


class TestClassifyReversibility:
    def test_ddl_is_always_permanent(self):
        c = _make_contract(operation_type=OperationType.DDL)
        rev, reason = _classify_reversibility(c, has_soft_delete=True)
        assert rev == ReversibilityClass.PERMANENT
        assert "DDL" in reason

    def test_external_triggers_make_permanent(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            primary_table="users",
            external_triggers=[
                ExternalTrigger(
                    trigger_name="send_email",
                    event="DELETE",
                    extension="pg_net",
                )
            ],
        )
        rev, reason = _classify_reversibility(c, has_soft_delete=True)
        assert rev == ReversibilityClass.PERMANENT
        assert "pg_net" in reason

    def test_delete_without_soft_delete_is_permanent(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            primary_table="users",
        )
        rev, reason = _classify_reversibility(c, has_soft_delete=False)
        assert rev == ReversibilityClass.PERMANENT
        assert "soft-delete" in reason

    def test_delete_with_soft_delete_is_reversible_automated(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            primary_table="users",
        )
        rev, reason = _classify_reversibility(c, has_soft_delete=True)
        assert rev == ReversibilityClass.REVERSIBLE_AUTOMATED
        assert "Soft-delete" in reason

    def test_insert_is_reversible_automated(self):
        c = _make_contract(operation_type=OperationType.INSERT)
        rev, _ = _classify_reversibility(c, has_soft_delete=False)
        assert rev == ReversibilityClass.REVERSIBLE_AUTOMATED

    def test_select_is_reversible_automated(self):
        c = _make_contract(operation_type=OperationType.SELECT)
        rev, _ = _classify_reversibility(c, has_soft_delete=False)
        assert rev == ReversibilityClass.REVERSIBLE_AUTOMATED

    def test_priority_ddl_over_external_triggers(self):
        # DDL should win over external trigger check (priority 1 > priority 2)
        c = _make_contract(
            operation_type=OperationType.DDL,
            external_triggers=[
                ExternalTrigger(trigger_name="t", event="DROP", extension="pg_net")
            ],
        )
        rev, reason = _classify_reversibility(c, has_soft_delete=False)
        assert rev == ReversibilityClass.PERMANENT
        assert "DDL" in reason  # DDL reason, not pg_net reason


class TestIdentifyPermanentComponents:
    def test_ddl_produces_components(self):
        c = _make_contract(
            operation_type=OperationType.DDL,
            reversibility=ReversibilityClass.PERMANENT,
        )
        comps = _identify_permanent_components(c)
        assert any("DDL" in comp for comp in comps)

    def test_external_trigger_in_components(self):
        c = _make_contract(
            operation_type=OperationType.DELETE,
            primary_table="users",
            reversibility=ReversibilityClass.PERMANENT,
            external_triggers=[
                ExternalTrigger(
                    trigger_name="send_email", event="DELETE", extension="pg_net"
                )
            ],
        )
        comps = _identify_permanent_components(c)
        assert any("pg_net" in comp for comp in comps)

    def test_reversible_no_permanent_components(self):
        c = _make_contract(
            operation_type=OperationType.UPDATE,
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
        )
        comps = _identify_permanent_components(c)
        assert comps == []
