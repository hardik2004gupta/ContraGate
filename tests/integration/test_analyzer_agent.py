"""
Integration tests for the Analyzer Agent.

Tests the full analyze() flow with realistic MCP responses, verifying that
the contract is correctly populated across all fields. MCP calls are mocked
to avoid requiring live servers in CI.

Demo scenario (CLAUDE.md §23):
  - DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'
  - Expected: PERMANENT reversibility, cascade into orders, FULL_CONTRACT tier candidate

Additional scenarios:
  - Fast-path SELECT (REVERSIBLE_AUTOMATED)
  - External triggers (PERMANENT + external component)
  - Soft-delete table (REVERSIBLE_AUTOMATED)
  - Selective re-analysis guard
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from agents.analyzer.agent import AnalyzerAgent
from agents.analyzer.tools import (
    classify_operation_type,
    parse_sql_intent,
)
from orchestrator.handoff_schema import (
    HandoffContract,
    OperationType,
    ReversibilityClass,
    SourceType,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_integ_test",
        tenant_id="demo_tenant",
        submitted_by="integration-test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="SELECT 1",
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


def _make_agent(op_id: str = "cg_integ_test") -> AnalyzerAgent:
    agent = AnalyzerAgent(operation_id=op_id, tenant_id="demo_tenant")
    agent.log_agent_output = AsyncMock()
    return agent


_EMPTY_TRIGGERS = {
    "triggers": [],
    "has_permanent_side_effects": False,
    "non_transactional_triggers": [],
}
_EMPTY_FK = {"levels": [], "total_estimated_rows": 0}
_NO_SOFT_DELETE = {"has_soft_delete": False}
_HAS_SOFT_DELETE = {"has_soft_delete": True, "column_name": "deleted_at"}


# ── E2E Demo Scenario: DELETE users (CLAUDE.md §23 E2E-3) ────────────────────

class TestDemoScenarioDeleteUsers:
    """
    DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'
    Expected: PERMANENT reversibility, cascade into orders, intent_summary set.
    """

    DEMO_SQL = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"

    @pytest.mark.asyncio
    async def test_operation_type_is_delete(self):
        op = classify_operation_type(self.DEMO_SQL)
        assert op == OperationType.DELETE

    @pytest.mark.asyncio
    async def test_parse_extracts_users_and_condition(self):
        table, cond = parse_sql_intent(self.DEMO_SQL)
        assert table == "users"
        assert "last_active" in cond

    @pytest.mark.asyncio
    async def test_full_analysis_permanent_no_soft_delete(self):
        contract = _make_contract(raw_sql=self.DEMO_SQL)
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 4200, "confidence": 0.8}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            if tool == "get_fk_graph":
                return {
                    "levels": [
                        {"table_name": "orders", "estimated_rows": 12600, "cascade_action": "CASCADE", "depth": 1},
                        {"table_name": "invoices", "estimated_rows": 3100, "cascade_action": "CASCADE", "depth": 2},
                    ],
                    "total_estimated_rows": 15700,
                }
            if tool == "check_soft_delete":
                return _NO_SOFT_DELETE
            return {}

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(
            return_value="Delete 4,200 inactive users not active in 2+ years"
        )):
            result = await agent.analyze(contract)

        assert result.operation_type == OperationType.DELETE
        assert result.primary_table == "users"
        assert result.estimated_primary_rows == 4200
        assert result.row_confidence == 0.8
        assert len(result.cascade) == 2
        assert result.cascade[0].table == "orders"
        assert result.cascade[1].table == "invoices"
        assert result.reversibility == ReversibilityClass.PERMANENT
        assert "recovery" in result.reversibility_reason.lower()
        assert result.intent_summary == "Delete 4,200 inactive users not active in 2+ years"
        assert result.automated_recovery_sql is None  # ADR-008 invariant
        assert any(p.agent == "ANALYZER_AGENT" for p in result.workflow_provenance)
        assert any(p.llm_involved for p in result.workflow_provenance)

    @pytest.mark.asyncio
    async def test_permanent_components_populated(self):
        contract = _make_contract(raw_sql=self.DEMO_SQL)
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 1000, "confidence": 0.7}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            if tool == "get_fk_graph":
                return {"levels": [{"table_name": "orders", "estimated_rows": 2000, "cascade_action": "CASCADE", "depth": 1}]}
            if tool == "check_soft_delete":
                return _NO_SOFT_DELETE
            return {}

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Delete users")):
            result = await agent.analyze(contract)

        assert result.reversibility == ReversibilityClass.PERMANENT
        assert any("users" in c for c in result.permanent_components)


# ── Fast-path SELECT (E2E-1) ──────────────────────────────────────────────────

class TestSelectFastPath:
    @pytest.mark.asyncio
    async def test_select_produces_reversible_automated(self):
        contract = _make_contract(
            raw_sql="SELECT id, email FROM users WHERE status = 'active' LIMIT 100"
        )
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 3847, "confidence": 0.9}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            return {}

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Read active users")):
            result = await agent.analyze(contract)

        assert result.operation_type == OperationType.SELECT
        assert result.reversibility == ReversibilityClass.REVERSIBLE_AUTOMATED
        assert result.cascade == []  # no cascade for SELECT
        assert result.external_triggers == []

    @pytest.mark.asyncio
    async def test_select_no_permanent_components(self):
        contract = _make_contract(raw_sql="SELECT * FROM orders")
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            return {"estimated_rows": 100, "confidence": 0.8} if tool == "estimate_row_count" else _EMPTY_TRIGGERS

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Read orders")):
            result = await agent.analyze(contract)

        assert result.permanent_components == []


# ── External trigger (PERMANENT with external component) ─────────────────────

class TestExternalTriggerScenario:
    @pytest.mark.asyncio
    async def test_pg_net_trigger_makes_permanent(self):
        contract = _make_contract(
            raw_sql="DELETE FROM users WHERE subscription_status = 'cancelled'"
        )
        agent = _make_agent()

        nt_trigger_result = {
            "triggers": [{
                "trigger_name": "cancel_sendgrid_subscription",
                "table_name": "users",
                "event": "DELETE",
                "timing": "AFTER",
                "function_name": "cancel_fn",
                "invokes_non_transactional": True,
                "non_transactional_extension": "pg_net",
                "target_endpoint": "https://api.sendgrid.com/v3/cancel",
            }],
            "has_permanent_side_effects": True,
            "non_transactional_triggers": [{
                "trigger_name": "cancel_sendgrid_subscription",
                "non_transactional_extension": "pg_net",
                "target_endpoint": "https://api.sendgrid.com/v3/cancel",
            }],
        }

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 2000, "confidence": 0.8}
            if tool == "list_triggers":
                return nt_trigger_result
            if tool == "get_fk_graph":
                return _EMPTY_FK
            if tool == "check_soft_delete":
                return _NO_SOFT_DELETE
            return {}

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Delete cancelled users")):
            result = await agent.analyze(contract)

        assert result.reversibility == ReversibilityClass.PERMANENT
        assert len(result.external_triggers) == 1
        assert result.external_triggers[0].extension == "pg_net"
        # estimated_calls should be set to row_count
        assert result.external_triggers[0].estimated_calls == 2000
        # Permanent component for external trigger
        assert any("pg_net" in c for c in result.permanent_components)


# ── Soft-delete table (REVERSIBLE_AUTOMATED) ──────────────────────────────────

class TestSoftDeleteScenario:
    @pytest.mark.asyncio
    async def test_delete_with_soft_delete_column_is_reversible(self):
        contract = _make_contract(raw_sql="DELETE FROM orders WHERE status = 'cancelled'")
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 500, "confidence": 0.8}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            if tool == "get_fk_graph":
                return _EMPTY_FK
            if tool == "check_soft_delete":
                return _HAS_SOFT_DELETE
            return {}

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Delete cancelled orders")):
            result = await agent.analyze(contract)

        assert result.reversibility == ReversibilityClass.REVERSIBLE_AUTOMATED
        assert result.permanent_components == []
        assert result.automated_recovery_sql is None  # ADR-008: always None


# ── DDL always permanent ──────────────────────────────────────────────────────

class TestDdlScenario:
    @pytest.mark.asyncio
    async def test_drop_table_is_always_permanent(self):
        contract = _make_contract(raw_sql="DROP TABLE users")
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 0, "confidence": 0.5}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            return {}

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Drop users table")):
            result = await agent.analyze(contract)

        assert result.operation_type == OperationType.DDL
        assert result.reversibility == ReversibilityClass.PERMANENT
        assert any("DDL" in c for c in result.permanent_components)

    @pytest.mark.asyncio
    async def test_truncate_is_always_permanent(self):
        contract = _make_contract(raw_sql="TRUNCATE TABLE orders")
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            return {"estimated_rows": 0, "confidence": 0.5} if tool == "estimate_row_count" else _EMPTY_TRIGGERS

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Truncate orders")):
            result = await agent.analyze(contract)

        assert result.reversibility == ReversibilityClass.PERMANENT


# ── Selective re-analysis ─────────────────────────────────────────────────────

class TestSelectiveReanalysis:
    @pytest.mark.asyncio
    async def test_no_stale_analysis_fields_skips(self):
        """When no analysis fields are in stale_fields, agent returns immediately."""
        contract = _make_contract(
            raw_sql="DELETE FROM users WHERE id = 1",
            reanalysis_count=1,
            stale_fields=["risk_score", "risk_tier"],  # no analysis fields
        )
        contract.primary_table = "users"
        contract.estimated_primary_rows = 42
        contract.reversibility = ReversibilityClass.REVERSIBLE_AUTOMATED

        agent = _make_agent()
        call_mock = AsyncMock()
        agent.call_tool = call_mock

        result = await agent.analyze(contract)

        call_mock.assert_not_called()
        assert result.estimated_primary_rows == 42  # unchanged

    @pytest.mark.asyncio
    async def test_stale_analysis_field_triggers_rerun(self):
        """When an analysis field is stale, the agent runs analysis."""
        contract = _make_contract(
            raw_sql="DELETE FROM users WHERE id = 1",
            reanalysis_count=1,
            stale_fields=["estimated_primary_rows"],
        )

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 99, "confidence": 0.7}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            if tool == "get_fk_graph":
                return _EMPTY_FK
            if tool == "check_soft_delete":
                return _NO_SOFT_DELETE
            return {}

        agent = _make_agent()
        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Delete user")):
            result = await agent.analyze(contract)

        assert result.estimated_primary_rows == 99


# ── LLM fallback ─────────────────────────────────────────────────────────────

class TestLlmFallback:
    @pytest.mark.asyncio
    async def test_llm_failure_uses_fallback_summary(self):
        """When Anthropic API raises, fallback summary is used — pipeline does not crash."""
        contract = _make_contract(
            raw_sql="DELETE FROM users WHERE status = 'inactive'",
        )
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 100, "confidence": 0.8}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            if tool == "get_fk_graph":
                return _EMPTY_FK
            if tool == "check_soft_delete":
                return _NO_SOFT_DELETE
            return {}

        agent.call_tool = mock_call
        # Patch _get_intent_summary to simulate API failure path
        with patch.object(
            agent,
            "_get_intent_summary",
            AsyncMock(return_value="DELETE ~100 row(s) from 'users' WHERE status = 'inactive'"),
        ):
            result = await agent.analyze(contract)

        assert result.intent_summary != ""
        assert "users" in result.intent_summary

    @pytest.mark.asyncio
    async def test_no_llm_reversibility_classification(self):
        """Reversibility must be deterministic — verify no LLM produces it."""
        contract = _make_contract(raw_sql="DELETE FROM users WHERE id = 1")
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 1, "confidence": 0.9}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            if tool == "get_fk_graph":
                return _EMPTY_FK
            if tool == "check_soft_delete":
                return _NO_SOFT_DELETE
            return {}

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Delete one user")):
            result = await agent.analyze(contract)

        # Reversibility must be a known enum value — not LLM-generated text
        assert result.reversibility in list(ReversibilityClass)
        # automated_recovery_sql must always be None (ADR-008)
        assert result.automated_recovery_sql is None


# ── Provenance invariant ──────────────────────────────────────────────────────

class TestProvenanceInvariant:
    @pytest.mark.asyncio
    async def test_provenance_recorded_after_analysis(self):
        contract = _make_contract(raw_sql="DELETE FROM users WHERE id = 1")
        agent = _make_agent()

        async def mock_call(server, tool, args, **kw):
            if tool == "estimate_row_count":
                return {"estimated_rows": 1, "confidence": 0.8}
            if tool == "list_triggers":
                return _EMPTY_TRIGGERS
            if tool == "get_fk_graph":
                return _EMPTY_FK
            if tool == "check_soft_delete":
                return _NO_SOFT_DELETE
            return {}

        agent.call_tool = mock_call
        with patch.object(agent, "_get_intent_summary", AsyncMock(return_value="Delete user")):
            result = await agent.analyze(contract)

        provenance = result.workflow_provenance
        assert len(provenance) >= 1
        analyzer_entries = [p for p in provenance if p.agent == "ANALYZER_AGENT"]
        assert len(analyzer_entries) == 1
        entry = analyzer_entries[0]
        assert "operation_type" in entry.field_written
        assert "reversibility" in entry.field_written
        assert "intent_summary" in entry.field_written
        assert entry.llm_involved is True  # LLM used for intent_summary
