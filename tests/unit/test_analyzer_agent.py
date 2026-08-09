"""
Unit tests for Analyzer Agent tools and agent behavior.

All tests run without MCP servers, PostgreSQL, or Anthropic API.
MCP calls are mocked via AsyncMock on BaseAgent.call_tool.

Coverage:
  - classify_operation_type: all SQL operation types
  - parse_sql_intent: DELETE, UPDATE, INSERT, SELECT, DDL
  - build_cascade_entries: MCP response transformation
  - build_external_triggers: non-transactional trigger extraction
  - build_trigger_analysis_from_mcp: TriggerAnalysis reconstruction
  - estimate_api_fanout: row-count-based call estimation
  - identify_permanent_components: per-reversibility-class output
  - AnalyzerAgent.analyze(): end-to-end with mocked call_tool
  - _fallback_intent_summary: rule-based summary format
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.analyzer.agent import AnalyzerAgent, _fallback_intent_summary
from agents.analyzer.tools import (
    build_cascade_entries,
    build_external_triggers,
    build_trigger_analysis_from_mcp,
    classify_operation_type,
    estimate_api_fanout,
    identify_permanent_components,
    parse_sql_intent,
)
from orchestrator.handoff_schema import (
    CascadeEntry,
    ExternalTrigger,
    HandoffContract,
    OperationType,
    ReversibilityClass,
    SourceType,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_unit_test",
        tenant_id="demo_tenant",
        submitted_by="test-runner",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="SELECT 1",
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


_EMPTY_TRIGGER_RESULT = {
    "triggers": [],
    "has_permanent_side_effects": False,
    "non_transactional_triggers": [],
}

_EMPTY_FK_RESULT = {
    "levels": [],
    "total_estimated_rows": 0,
}


# ── classify_operation_type ───────────────────────────────────────────────────

class TestClassifyOperationType:
    def test_delete(self):
        assert classify_operation_type("DELETE FROM users WHERE id = 1") == OperationType.DELETE

    def test_update(self):
        assert classify_operation_type("UPDATE orders SET status = 'x'") == OperationType.UPDATE

    def test_insert(self):
        assert classify_operation_type("INSERT INTO notifications (id) VALUES (1)") == OperationType.INSERT

    def test_select(self):
        assert classify_operation_type("SELECT * FROM users") == OperationType.SELECT

    def test_drop_table(self):
        assert classify_operation_type("DROP TABLE users") == OperationType.DDL

    def test_truncate(self):
        assert classify_operation_type("TRUNCATE TABLE orders") == OperationType.DDL

    def test_alter(self):
        assert classify_operation_type("ALTER TABLE users ADD COLUMN x TEXT") == OperationType.DDL

    def test_create(self):
        assert classify_operation_type("CREATE TABLE new_table (id SERIAL PRIMARY KEY)") == OperationType.DDL

    def test_empty_sql(self):
        assert classify_operation_type("") == OperationType.UNKNOWN

    def test_case_insensitive_delete(self):
        assert classify_operation_type("delete from users") == OperationType.DELETE

    def test_whitespace_handling(self):
        assert classify_operation_type("  SELECT  1  ") == OperationType.SELECT


# ── parse_sql_intent ──────────────────────────────────────────────────────────

class TestParseSqlIntent:
    def test_delete_with_where(self):
        table, cond = parse_sql_intent(
            "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        )
        assert table == "users"
        assert "last_active" in cond

    def test_select_from(self):
        table, cond = parse_sql_intent("SELECT * FROM orders WHERE status = 'pending'")
        assert table == "orders"
        assert "status" in cond

    def test_update_with_where(self):
        table, cond = parse_sql_intent("UPDATE users SET name = 'x' WHERE id = 5")
        assert table == "users"
        assert "id = 5" in cond

    def test_insert_into(self):
        table, cond = parse_sql_intent("INSERT INTO notifications (user_id) VALUES (1)")
        assert table == "notifications"
        assert cond == ""

    def test_drop_table(self):
        table, cond = parse_sql_intent("DROP TABLE users")
        assert table == "users"
        assert cond == ""

    def test_truncate_table(self):
        table, cond = parse_sql_intent("TRUNCATE TABLE orders")
        assert table == "orders"

    def test_delete_no_where(self):
        table, cond = parse_sql_intent("DELETE FROM users")
        assert table == "users"
        assert cond == ""

    def test_select_no_where(self):
        table, cond = parse_sql_intent("SELECT id FROM invoices")
        assert table == "invoices"
        assert cond == ""

    def test_empty_returns_empty(self):
        table, cond = parse_sql_intent("")
        assert table == ""
        assert cond == ""

    def test_where_stops_at_order_by(self):
        table, cond = parse_sql_intent(
            "DELETE FROM users WHERE id > 100 ORDER BY id LIMIT 10"
        )
        assert "ORDER" not in cond
        assert "id > 100" in cond

    def test_case_insensitive(self):
        table, cond = parse_sql_intent("delete from Users where status = 'x'")
        assert table == "Users"  # preserves original case


# ── build_cascade_entries ─────────────────────────────────────────────────────

class TestBuildCascadeEntries:
    def test_empty_levels(self):
        result = build_cascade_entries({"levels": []}, root_rows=100)
        assert result == []

    def test_single_level(self):
        fk_result = {
            "levels": [{
                "table_name": "orders",
                "estimated_rows": 400,
                "cascade_action": "CASCADE",
                "depth": 1,
            }]
        }
        entries = build_cascade_entries(fk_result, root_rows=100)
        assert len(entries) == 1
        assert entries[0].table == "orders"
        assert entries[0].estimated_rows == 400
        assert entries[0].cascade_action == "CASCADE"
        assert entries[0].depth == 1
        assert entries[0].actual_rows is None

    def test_multi_level(self):
        fk_result = {
            "levels": [
                {"table_name": "orders", "estimated_rows": 200, "cascade_action": "CASCADE", "depth": 1},
                {"table_name": "invoices", "estimated_rows": 50, "cascade_action": "CASCADE", "depth": 2},
            ]
        }
        entries = build_cascade_entries(fk_result, root_rows=100)
        assert len(entries) == 2
        assert entries[1].table == "invoices"
        assert entries[1].depth == 2


# ── build_external_triggers ───────────────────────────────────────────────────

class TestBuildExternalTriggers:
    def test_no_triggers(self):
        result = build_external_triggers(_EMPTY_TRIGGER_RESULT)
        assert result == []

    def test_non_transactional_trigger(self):
        trigger_result = {
            "triggers": [{
                "trigger_name": "send_email_on_delete",
                "event": "DELETE",
                "invokes_non_transactional": True,
                "non_transactional_extension": "pg_net",
                "target_endpoint": "https://api.example.com/notify",
            }],
            "non_transactional_triggers": [{
                "trigger_name": "send_email_on_delete",
                "non_transactional_extension": "pg_net",
                "target_endpoint": "https://api.example.com/notify",
            }],
            "has_permanent_side_effects": True,
        }
        result = build_external_triggers(trigger_result)
        assert len(result) == 1
        assert result[0].trigger_name == "send_email_on_delete"
        assert result[0].extension == "pg_net"
        assert result[0].event == "DELETE"
        assert result[0].target_endpoint == "https://api.example.com/notify"

    def test_transactional_trigger_excluded(self):
        trigger_result = {
            "triggers": [{"trigger_name": "audit_log", "event": "INSERT", "invokes_non_transactional": False}],
            "non_transactional_triggers": [],
            "has_permanent_side_effects": False,
        }
        result = build_external_triggers(trigger_result)
        assert result == []


# ── build_trigger_analysis_from_mcp ──────────────────────────────────────────

class TestBuildTriggerAnalysisFromMcp:
    def test_empty_result(self):
        ta = build_trigger_analysis_from_mcp("users", _EMPTY_TRIGGER_RESULT)
        assert ta.table == "users"
        assert ta.triggers == []
        assert ta.has_permanent_side_effects is False
        assert ta.non_transactional_triggers == []

    def test_non_transactional_detected(self):
        trigger_result = {
            "triggers": [{
                "trigger_name": "notify",
                "table_name": "users",
                "event": "DELETE",
                "timing": "AFTER",
                "function_name": "notify_fn",
                "invokes_non_transactional": True,
                "non_transactional_extension": "pg_net",
                "target_endpoint": None,
            }],
            "non_transactional_triggers": [{"trigger_name": "notify"}],
            "has_permanent_side_effects": True,
        }
        ta = build_trigger_analysis_from_mcp("users", trigger_result)
        assert ta.has_permanent_side_effects is True
        assert len(ta.non_transactional_triggers) == 1
        assert ta.non_transactional_triggers[0].trigger_name == "notify"
        assert ta.non_transactional_triggers[0].non_transactional_extension == "pg_net"


# ── estimate_api_fanout ───────────────────────────────────────────────────────

class TestEstimateApiFanout:
    def test_no_triggers(self):
        result = estimate_api_fanout([], row_count=1000)
        assert result == []

    def test_single_trigger(self):
        trigger = ExternalTrigger(trigger_name="t", event="DELETE", extension="pg_net")
        result = estimate_api_fanout([trigger], row_count=500)
        assert len(result) == 1
        assert result[0].estimated_calls == 500

    def test_does_not_mutate_input(self):
        trigger = ExternalTrigger(trigger_name="t", event="DELETE", extension="pg_net")
        original_calls = trigger.estimated_calls
        estimate_api_fanout([trigger], row_count=999)
        assert trigger.estimated_calls == original_calls  # unchanged

    def test_multiple_triggers(self):
        triggers = [
            ExternalTrigger(trigger_name="t1", event="DELETE", extension="pg_net"),
            ExternalTrigger(trigger_name="t2", event="DELETE", extension="dblink"),
        ]
        result = estimate_api_fanout(triggers, row_count=100)
        assert all(t.estimated_calls == 100 for t in result)


# ── identify_permanent_components ────────────────────────────────────────────

class TestIdentifyPermanentComponents:
    def test_reversible_returns_empty(self):
        result = identify_permanent_components(
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
            operation_type=OperationType.DELETE,
            primary_table="users",
            external_triggers=[],
            cascade=[],
        )
        assert result == []

    def test_ddl_permanent(self):
        result = identify_permanent_components(
            reversibility=ReversibilityClass.PERMANENT,
            operation_type=OperationType.DDL,
            primary_table="users",
            external_triggers=[],
            cascade=[],
        )
        assert any("DDL" in c for c in result)

    def test_external_trigger_in_components(self):
        trigger = ExternalTrigger(trigger_name="notify", event="DELETE", extension="pg_net")
        result = identify_permanent_components(
            reversibility=ReversibilityClass.PERMANENT,
            operation_type=OperationType.DELETE,
            primary_table="users",
            external_triggers=[trigger],
            cascade=[],
        )
        assert any("pg_net" in c for c in result)

    def test_cascade_in_components(self):
        cascade = [CascadeEntry(table="orders", estimated_rows=500, cascade_action="CASCADE", depth=1)]
        result = identify_permanent_components(
            reversibility=ReversibilityClass.PERMANENT,
            operation_type=OperationType.DELETE,
            primary_table="users",
            external_triggers=[],
            cascade=cascade,
        )
        assert any("orders" in c for c in result)


# ── AnalyzerAgent end-to-end (mocked MCP) ────────────────────────────────────

class TestAnalyzerAgentAnalyze:
    """Full analyze() flow with call_tool mocked — no network calls."""

    def _make_agent(self) -> AnalyzerAgent:
        return AnalyzerAgent(operation_id="cg_test001", tenant_id="demo_tenant")

    @pytest.mark.asyncio
    async def test_demo_scenario_delete_users(self):
        """Demo scenario: DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'."""
        contract = _make_contract(
            raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        )

        async def mock_call_tool(server, tool, args, **kwargs):
            if tool == "estimate_row_count":
                return {"estimated_rows": 4200, "confidence": 0.8}
            if tool == "list_triggers":
                return _EMPTY_TRIGGER_RESULT
            if tool == "get_fk_graph":
                return {
                    "levels": [
                        {"table_name": "orders", "estimated_rows": 12000, "cascade_action": "CASCADE", "depth": 1}
                    ],
                    "total_estimated_rows": 12000,
                }
            if tool == "check_soft_delete":
                return {"has_soft_delete": False}
            return {}

        agent = self._make_agent()
        agent.call_tool = mock_call_tool
        agent.log_agent_output = AsyncMock()

        with patch("agents.analyzer.agent.classify_reversibility") as mock_rev:
            from sql_analysis_lib.reversibility_rules import ReversibilityClass as LibRC, ReversibilityResult
            mock_rev.return_value = ReversibilityResult(
                classification=LibRC.PERMANENT,
                reason="No soft-delete column",
                automated_recovery_sql=None,
                pitr_available=False,
                pitr_window_hours=None,
                permanent_components=["no_recovery_path_for_users"],
            )
            with patch("agents.analyzer.agent._fallback_intent_summary") as mock_summary:
                mock_summary.return_value = "Delete 4,200 inactive users"
                with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Delete 4,200 inactive users")):
                    result = await agent.analyze(contract)

        assert result.operation_type == OperationType.DELETE
        assert result.primary_table == "users"
        assert "last_active" in result.condition
        assert result.estimated_primary_rows == 4200
        assert result.row_confidence == 0.8
        assert len(result.cascade) == 1
        assert result.cascade[0].table == "orders"
        assert result.reversibility == ReversibilityClass.PERMANENT
        assert result.intent_summary != ""
        # Provenance must be recorded
        assert any(p.agent == "ANALYZER_AGENT" for p in result.workflow_provenance)

    @pytest.mark.asyncio
    async def test_select_produces_reversible_automated(self):
        contract = _make_contract(
            raw_sql="SELECT * FROM orders WHERE status = 'pending'",
        )

        async def mock_call_tool(server, tool, args, **kwargs):
            if tool == "estimate_row_count":
                return {"estimated_rows": 42, "confidence": 0.9}
            if tool == "list_triggers":
                return _EMPTY_TRIGGER_RESULT
            return {}

        agent = self._make_agent()
        agent.call_tool = mock_call_tool
        agent.log_agent_output = AsyncMock()

        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Read 42 pending orders")):
            result = await agent.analyze(contract)

        assert result.operation_type == OperationType.SELECT
        assert result.reversibility == ReversibilityClass.REVERSIBLE_AUTOMATED
        assert result.cascade == []

    @pytest.mark.asyncio
    async def test_mcp_failure_graceful_degradation(self):
        """When postgres-reader is unavailable, analysis completes with zero estimates."""
        from orchestrator.mcp_client import MCPCallError

        contract = _make_contract(
            raw_sql="DELETE FROM users WHERE id = 1",
        )

        async def mock_call_tool(server, tool, args, **kwargs):
            raise MCPCallError("postgres-reader", tool, "connection refused")

        agent = self._make_agent()
        agent.call_tool = mock_call_tool
        agent.log_agent_output = AsyncMock()

        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Delete 1 user")):
            result = await agent.analyze(contract)

        # Pipeline must not crash
        assert result.estimated_primary_rows == 0
        assert result.cascade == []
        assert result.external_triggers == []

    @pytest.mark.asyncio
    async def test_ddl_classified_permanent(self):
        contract = _make_contract(raw_sql="DROP TABLE users")

        async def mock_call_tool(server, tool, args, **kwargs):
            if tool == "estimate_row_count":
                return {"estimated_rows": 0, "confidence": 0.5}
            if tool == "list_triggers":
                return _EMPTY_TRIGGER_RESULT
            return {}

        agent = self._make_agent()
        agent.call_tool = mock_call_tool
        agent.log_agent_output = AsyncMock()

        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Drop users table")):
            result = await agent.analyze(contract)

        assert result.operation_type == OperationType.DDL
        assert result.reversibility == ReversibilityClass.PERMANENT
        assert any("DDL" in c for c in result.permanent_components)

    @pytest.mark.asyncio
    async def test_selective_reanalysis_skips_fresh_fields(self):
        """Selective re-analysis: no stale analysis fields → return contract unchanged."""
        contract = _make_contract(
            raw_sql="DELETE FROM users WHERE id = 1",
            reanalysis_count=1,
            stale_fields=["risk_score"],  # no analysis fields stale
        )
        contract.primary_table = "users"
        contract.estimated_primary_rows = 99

        agent = self._make_agent()
        agent.call_tool = AsyncMock()
        agent.log_agent_output = AsyncMock()

        result = await agent.analyze(contract)

        # call_tool must not have been called
        agent.call_tool.assert_not_called()
        assert result.estimated_primary_rows == 99  # unchanged


# ── _fallback_intent_summary ──────────────────────────────────────────────────

class TestFallbackIntentSummary:
    def test_delete_without_cascade(self):
        contract = _make_contract(
            raw_sql="DELETE FROM users WHERE id = 1",
            operation_type=OperationType.DELETE,
            primary_table="users",
            condition="id = 1",
            estimated_primary_rows=1,
        )
        summary = _fallback_intent_summary(contract)
        assert "DELETE" in summary
        assert "users" in summary
        assert "1" in summary

    def test_includes_cascade_total(self):
        contract = _make_contract(
            raw_sql="DELETE FROM users",
            operation_type=OperationType.DELETE,
            primary_table="users",
            estimated_primary_rows=100,
            cascade=[
                CascadeEntry(table="orders", estimated_rows=500, cascade_action="CASCADE", depth=1)
            ],
        )
        summary = _fallback_intent_summary(contract)
        assert "500" in summary

    def test_unknown_table_handled(self):
        contract = _make_contract(raw_sql="FOOBAR")
        summary = _fallback_intent_summary(contract)
        assert "unknown table" in summary
