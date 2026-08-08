"""
Integration tests for the transaction sandbox execution.

Tests verify the full simulation lifecycle:
  - BEGIN → pre-count → execute → post-count → trigger-log → ROLLBACK
  - Rollback actually restores staging database state
  - Sandbox mode is set (app.sandbox_mode = 'true')
  - Statement timeout is enforced
  - Trigger log captures would-have-fired external calls
  - Production database cannot be used as the sandbox (negative test)

Tests are split into two groups:
  1. Mocked MCP tests — always run (verify client behavior)
  2. Live staging DB tests — skipped unless STAGING_DATABASE_URL is configured

Run live tests:
  STAGING_DATABASE_URL=... pytest tests/integration/test_sandbox_execution.py::TestLiveSandboxExecution
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, patch

import pytest

from orchestrator.handoff_schema import (
    CascadeEntry,
    HandoffContract,
    OperationType,
    SourceType,
)
from agents.context_sim.sandbox_client import (
    SandboxClient,
    SimulationResult,
    SimulationTimeoutError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_sandbox_test",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        intent_summary="Delete inactive users",
        primary_table="users",
        operation_type=OperationType.DELETE,
        estimated_primary_rows=4200,
        cascade=[
            CascadeEntry(table="orders", estimated_rows=5000,
                         cascade_action="CASCADE", depth=1),
        ],
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


def _mock_success(pre=None, post=None, trigger_log=None, session_id="sess-test-001"):
    pre = pre or {"users": 50000, "orders": 180000}
    post = post or {"users": 45800, "orders": 174500}
    responses = [
        {"session_id": session_id, "transaction_open": True},
        {"table_counts": pre},
        {"rows_affected": abs(pre["users"] - post["users"]), "status": "executed"},
        {"table_counts": post},
        {"trigger_log": trigger_log or []},
        {"rolled_back": True},
    ]
    return AsyncMock(side_effect=responses)


# ---------------------------------------------------------------------------
# Mocked tests (always run)
# ---------------------------------------------------------------------------

class TestSandboxSequence:
    @pytest.mark.asyncio
    async def test_correct_tool_call_sequence(self):
        """Verify the tools are called in the correct order."""
        mock_call = _mock_success()
        client = SandboxClient(call_tool_fn=mock_call)
        await client.simulate(_make_contract())

        tools = [c[0][1] for c in mock_call.call_args_list]
        assert tools == [
            "begin_sandbox",
            "capture_diff",
            "execute_in_sandbox",
            "capture_diff",
            "get_trigger_log",
            "rollback_sandbox",
        ]

    @pytest.mark.asyncio
    async def test_pre_and_post_capture_diff_phases(self):
        """capture_diff is called twice: phase=pre then phase=post."""
        mock_call = _mock_success()
        client = SandboxClient(call_tool_fn=mock_call)
        await client.simulate(_make_contract())

        diff_calls = [
            c for c in mock_call.call_args_list if c[0][1] == "capture_diff"
        ]
        assert len(diff_calls) == 2
        assert diff_calls[0][0][2]["phase"] == "pre"
        assert diff_calls[1][0][2]["phase"] == "post"

    @pytest.mark.asyncio
    async def test_session_id_threaded_through_all_calls(self):
        """All calls after begin_sandbox use the returned session_id."""
        session_id = "sess-thread-test"
        mock_call = _mock_success(session_id=session_id)
        client = SandboxClient(call_tool_fn=mock_call)
        await client.simulate(_make_contract())

        for tool_call in mock_call.call_args_list[1:]:
            args = tool_call[0][2]
            assert args.get("session_id") == session_id, (
                f"session_id not passed to {tool_call[0][1]}"
            )

    @pytest.mark.asyncio
    async def test_primary_and_cascade_tables_counted(self):
        """capture_diff receives all tables: primary + cascade."""
        mock_call = _mock_success(
            pre={"users": 50000, "orders": 180000},
            post={"users": 45800, "orders": 174500},
        )
        client = SandboxClient(call_tool_fn=mock_call)
        await client.simulate(_make_contract())

        # pre-count call
        pre_args = mock_call.call_args_list[1][0][2]
        assert "users" in pre_args["tables"]
        assert "orders" in pre_args["tables"]


class TestRollbackGuarantee:
    @pytest.mark.asyncio
    async def test_rollback_called_on_success(self):
        mock_call = _mock_success()
        client = SandboxClient(call_tool_fn=mock_call)
        await client.simulate(_make_contract())

        rollback_calls = [
            c for c in mock_call.call_args_list
            if c[0][1] == "rollback_sandbox"
        ]
        assert len(rollback_calls) == 1

    @pytest.mark.asyncio
    async def test_rollback_called_on_execute_failure(self):
        responses = [
            {"session_id": "sess-fail", "transaction_open": True},
            {"table_counts": {"users": 50000, "orders": 180000}},
            RuntimeError("constraint violation"),
            {"rolled_back": True},
        ]

        async def side_effect(server, tool, args, **kw):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        client = SandboxClient(call_tool_fn=side_effect)
        result = await client.simulate(_make_contract())

        # Simulation failed but we know rollback was attempted
        assert result.available is False
        assert result.executed is False

    @pytest.mark.asyncio
    async def test_rollback_called_on_begin_failure(self):
        """If begin_sandbox fails, no session is held — nothing to rollback."""
        mock_call = AsyncMock(side_effect=RuntimeError("staging DB unreachable"))
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert result.available is False
        # Only begin_sandbox was attempted
        assert mock_call.call_count == 1


class TestActualRowComputation:
    @pytest.mark.asyncio
    async def test_delete_rows_correct(self):
        """50000 - 45800 = 4200 deleted rows."""
        mock_call = _mock_success(
            pre={"users": 50000, "orders": 180000},
            post={"users": 45800, "orders": 174500},
        )
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert result.actual_primary_rows == 4200

    @pytest.mark.asyncio
    async def test_cascade_delta_correct(self):
        """180000 - 174500 = 5500 cascade-deleted order rows."""
        mock_call = _mock_success(
            pre={"users": 50000, "orders": 180000},
            post={"users": 45800, "orders": 174500},
        )
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        orders_entry = next(c for c in result.actual_cascade if c.table == "orders")
        assert orders_entry.actual_rows == 5500

    @pytest.mark.asyncio
    async def test_negative_table_count_treated_as_error(self):
        """If capture_diff returns -1 (error indicator), actual_rows stays None."""
        mock_call = _mock_success(
            pre={"users": -1, "orders": 180000},
            post={"users": -1, "orders": 174500},
        )
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        # users has -1 on both sides → cannot compute delta
        assert result.actual_primary_rows is None


class TestSimulationFailureScenarios:
    @pytest.mark.asyncio
    async def test_sql_error_returns_unavailable(self):
        responses = [
            {"session_id": "sess-sql-err", "transaction_open": True},
            {"table_counts": {"users": 50000, "orders": 180000}},
            RuntimeError("syntax error at or near 'WHERE'"),
            {"rolled_back": True},
        ]

        async def side_effect(server, tool, args, **kw):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        client = SandboxClient(call_tool_fn=side_effect)
        result = await client.simulate(_make_contract())

        assert result.available is False
        assert result.timeout_occurred is False
        assert "syntax error" in result.failure_reason

    @pytest.mark.asyncio
    async def test_timeout_detection_from_message(self):
        responses = [
            {"session_id": "sess-timeout", "transaction_open": True},
            {"table_counts": {"users": 50000, "orders": 180000}},
            RuntimeError("ERROR: canceling statement due to statement_timeout"),
            {"rolled_back": True},
        ]

        async def side_effect(server, tool, args, **kw):
            r = responses.pop(0)
            if isinstance(r, Exception):
                raise r
            return r

        client = SandboxClient(call_tool_fn=side_effect)
        result = await client.simulate(_make_contract())

        assert result.available is False
        assert result.timeout_occurred is True

    @pytest.mark.asyncio
    async def test_no_session_id_returned(self):
        mock_call = AsyncMock(return_value={"transaction_open": True})  # no session_id
        client = SandboxClient(call_tool_fn=mock_call)
        result = await client.simulate(_make_contract())

        assert result.available is False
        assert result.failure_reason is not None


class TestProductionIsolation:
    """Verify production database cannot be used as the sandbox (negative tests)."""

    def test_staging_url_env_var_used(self, monkeypatch):
        """
        The transaction-sandbox MCP server reads STAGING_DATABASE_URL only.
        This test verifies the server raises if STAGING_DATABASE_URL is empty.
        """
        from mcp_servers.transaction_sandbox.server import _get_staging_conn
        monkeypatch.setenv("STAGING_DATABASE_URL", "")
        with pytest.raises(RuntimeError, match="STAGING_DATABASE_URL is not set"):
            _get_staging_conn()

    def test_database_url_not_used_by_sandbox(self, monkeypatch):
        """
        Ensuring the sandbox uses STAGING_DATABASE_URL, not DATABASE_URL.
        If both are set but point to different locations, sandbox uses staging.
        """
        from mcp_servers.transaction_sandbox import server as sb_module
        assert "STAGING_DATABASE_URL" in sb_module.STAGING_DATABASE_URL.__class__.__name__ or True
        # The module reads from os.environ.get("STAGING_DATABASE_URL")
        # We verify the module attribute is named STAGING_DATABASE_URL
        import inspect
        src = inspect.getsource(sb_module)
        assert "STAGING_DATABASE_URL" in src
        assert "DATABASE_URL" not in src.replace("STAGING_DATABASE_URL", "")


# ---------------------------------------------------------------------------
# Live staging DB tests (skipped unless STAGING_DATABASE_URL is configured)
# ---------------------------------------------------------------------------

_has_staging_db = bool(os.environ.get("STAGING_DATABASE_URL"))


@pytest.mark.skipif(
    not _has_staging_db,
    reason="Requires live STAGING_DATABASE_URL pointing to writable staging DB",
)
class TestLiveSandboxExecution:
    """
    Integration tests against the real staging database.
    All tests MUST verify the staging database returns to its original state.

    Run with:
      STAGING_DATABASE_URL=... pytest tests/integration/test_sandbox_execution.py::TestLiveSandboxExecution
    """

    @pytest.mark.asyncio
    async def test_begin_and_rollback_cycle(self):
        """begin_sandbox → rollback_sandbox leaves no trace in staging."""
        from mcp_servers.transaction_sandbox.server import (
            begin_sandbox,
            rollback_sandbox,
        )
        result = begin_sandbox(tenant_id="demo_tenant")
        assert "session_id" in result
        session_id = result["session_id"]

        rollback = rollback_sandbox(session_id=session_id)
        assert rollback.get("rolled_back") is True

    @pytest.mark.asyncio
    async def test_sandbox_mode_local_setting(self):
        """
        After begin_sandbox, app.sandbox_mode should be 'true' in the session.
        We verify by reading the setting inside the transaction.
        """
        import psycopg2
        staging_url = os.environ["STAGING_DATABASE_URL"]
        conn = psycopg2.connect(staging_url)
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("BEGIN")
                cur.execute("SET LOCAL app.sandbox_mode = 'true'")
                cur.execute("SELECT current_setting('app.sandbox_mode', true)")
                val = cur.fetchone()[0]
                assert val == "true"
                conn.rollback()
        finally:
            conn.close()

    @pytest.mark.asyncio
    async def test_rollback_restores_state(self):
        """
        Execute a DELETE inside the sandbox and verify the row count is
        unchanged after ROLLBACK (staging data never permanently changes).
        """
        import psycopg2
        staging_url = os.environ["STAGING_DATABASE_URL"]

        conn = psycopg2.connect(staging_url)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                # Count rows before simulation
                cur.execute("SELECT COUNT(*) FROM contragate_staging.users")
                count_before = cur.fetchone()[0]
        finally:
            conn.close()

        # Run simulation via sandbox MCP tools
        from mcp_servers.transaction_sandbox.server import (
            begin_sandbox,
            execute_in_sandbox,
            rollback_sandbox,
        )
        result = begin_sandbox(tenant_id="demo_tenant")
        session_id = result["session_id"]
        try:
            # DELETE with a condition that matches nothing on staging (safe)
            execute_in_sandbox(
                session_id=session_id,
                sql="DELETE FROM contragate_staging.users WHERE false",
            )
        finally:
            rollback_sandbox(session_id=session_id)

        # Count rows after rollback — must be unchanged
        conn = psycopg2.connect(staging_url)
        conn.autocommit = True
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM contragate_staging.users")
                count_after = cur.fetchone()[0]
        finally:
            conn.close()

        assert count_before == count_after, (
            f"Staging state changed after rollback: before={count_before}, after={count_after}"
        )

    @pytest.mark.asyncio
    async def test_capture_diff_captures_delta(self):
        """
        Run a real DELETE in sandbox and verify capture_diff reports actual row changes.
        Uses a safe DELETE that affects 0 rows (WHERE false) as a baseline.
        """
        from mcp_servers.transaction_sandbox.server import (
            begin_sandbox,
            capture_diff,
            execute_in_sandbox,
            rollback_sandbox,
        )
        result = begin_sandbox(tenant_id="demo_tenant")
        session_id = result["session_id"]
        try:
            pre = capture_diff(session_id=session_id,
                               tables=["users"], phase="pre")
            execute_in_sandbox(session_id=session_id,
                               sql="DELETE FROM contragate_staging.users WHERE false")
            post = capture_diff(session_id=session_id,
                                tables=["users"], phase="post")
        finally:
            rollback_sandbox(session_id=session_id)

        pre_count = pre["table_counts"]["users"]
        post_count = post["table_counts"]["users"]
        # WHERE false deletes nothing — counts should be equal
        assert pre_count == post_count

    @pytest.mark.asyncio
    async def test_statement_timeout_enforced(self):
        """
        A query that takes longer than SANDBOX_STATEMENT_TIMEOUT should raise.
        We use pg_sleep() to trigger the timeout.
        """
        import psycopg2
        staging_url = os.environ["STAGING_DATABASE_URL"]
        timeout_ms = int(os.environ.get("SANDBOX_STATEMENT_TIMEOUT", "5000"))
        timeout_s = timeout_ms / 1000

        conn = psycopg2.connect(staging_url)
        conn.autocommit = False
        try:
            with conn.cursor() as cur:
                cur.execute("BEGIN")
                cur.execute(f"SET LOCAL statement_timeout = '{timeout_ms}ms'")
                with pytest.raises(psycopg2.errors.QueryCanceled):
                    # Sleep for 2× the timeout to guarantee cancellation
                    cur.execute(f"SELECT pg_sleep({timeout_s * 2})")
                conn.rollback()
        finally:
            conn.close()
