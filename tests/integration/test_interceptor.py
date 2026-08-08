"""
Tests for proxy interceptor: tool call parsing and manifest extraction.

No external dependencies — pure parsing logic.
"""

import pytest

from proxy.interceptor import InterceptionError, ToolCallManifest, intercept


class TestIntercept:
    def _call(self, body: dict, **kwargs) -> ToolCallManifest:
        return intercept(
            body=body,
            caller_id=kwargs.get("caller_id", "test_agent"),
            source_type=kwargs.get("source_type", "internal_system"),
            tenant_id=kwargs.get("tenant_id", "demo_tenant"),
        )

    def test_simple_format_sql(self):
        manifest = self._call({"tool_name": "execute_query", "sql": "SELECT 1"})
        assert manifest.sql == "SELECT 1"
        assert manifest.tool_name == "execute_query"

    def test_simple_format_query_alias(self):
        manifest = self._call({"tool_name": "execute_query", "query": "SELECT * FROM users"})
        assert manifest.sql == "SELECT * FROM users"

    def test_jsonrpc_format(self):
        body = {
            "method": "tools/call",
            "params": {
                "name": "execute_query",
                "arguments": {"sql": "DELETE FROM users WHERE id = 1"},
            },
        }
        manifest = self._call(body)
        assert manifest.sql == "DELETE FROM users WHERE id = 1"
        assert manifest.tool_name == "execute_query"

    def test_tool_format(self):
        body = {
            "tool": "run_sql",
            "args": {"sql": "UPDATE orders SET status = 'cancelled' WHERE id = 5"},
        }
        manifest = self._call(body)
        assert manifest.sql == "UPDATE orders SET status = 'cancelled' WHERE id = 5"

    def test_missing_sql_raises(self):
        with pytest.raises(InterceptionError):
            self._call({"tool_name": "execute_query", "description": "no sql here"})

    def test_empty_sql_raises(self):
        with pytest.raises(InterceptionError):
            self._call({"tool_name": "execute_query", "sql": "   "})

    def test_source_type_preserved(self):
        manifest = self._call(
            {"sql": "SELECT 1"},
            source_type="external_user_input",
        )
        assert manifest.source_type == "external_user_input"

    def test_caller_id_is_submitted_by(self):
        manifest = self._call(
            {"sql": "SELECT 1"},
            caller_id="langchain-agent",
        )
        assert manifest.submitted_by == "langchain-agent"

    def test_tenant_id_preserved(self):
        manifest = self._call({"sql": "SELECT 1"}, tenant_id="custom_tenant")
        assert manifest.tenant_id == "custom_tenant"

    def test_to_dict_contains_required_fields(self):
        manifest = self._call({"sql": "SELECT 1"})
        d = manifest.to_dict()
        assert "sql" in d
        assert "tool_name" in d
        assert "source_type" in d
        assert "tenant_id" in d
        assert "submitted_by" in d

    def test_is_write_operation_delete(self):
        manifest = self._call({"sql": "DELETE FROM users WHERE id = 1"})
        assert manifest.is_write_operation() is True

    def test_is_write_operation_select(self):
        manifest = self._call({"sql": "SELECT * FROM users"})
        assert manifest.is_write_operation() is False

    def test_is_write_operation_update(self):
        manifest = self._call({"sql": "UPDATE users SET name = 'x' WHERE id = 1"})
        assert manifest.is_write_operation() is True

    def test_is_write_operation_ddl(self):
        manifest = self._call({"sql": "DROP TABLE users"})
        assert manifest.is_write_operation() is True

    def test_intent_extracted_from_args(self):
        body = {
            "tool_name": "execute_query",
            "sql": "DELETE FROM users WHERE status = 'inactive'",
            "intent": "Remove inactive users from the database",
        }
        manifest = self._call(body)
        assert manifest.intent == "Remove inactive users from the database"

    def test_intent_none_when_not_provided(self):
        manifest = self._call({"sql": "SELECT 1"})
        assert manifest.intent is None
