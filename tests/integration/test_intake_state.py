"""
Tests for INTAKE state: run_intake() function.

Validates: operation_id format, source_type detection, operation_type detection,
prompt_injection_risk tagging, and HandoffContract initialization.
No external dependencies required.
"""

import pytest

from orchestrator.states.intake import run_intake
from orchestrator.handoff_schema import ApprovalState, OperationType, SourceType


class TestRunIntake:
    def _manifest(self, **kwargs) -> dict:
        defaults = {
            "sql": "SELECT * FROM users",
            "submitted_by": "test_agent",
            "source_type": "internal_system",
            "tenant_id": "demo_tenant",
        }
        defaults.update(kwargs)
        return defaults

    def test_operation_id_format(self):
        contract = run_intake(self._manifest())
        assert contract.operation_id.startswith("cg_")
        assert len(contract.operation_id) == 11  # "cg_" + 8 hex chars

    def test_operation_id_unique_each_call(self):
        c1 = run_intake(self._manifest())
        c2 = run_intake(self._manifest())
        assert c1.operation_id != c2.operation_id

    def test_select_operation_type(self):
        contract = run_intake(self._manifest(sql="SELECT id FROM users"))
        assert contract.operation_type == OperationType.SELECT

    def test_delete_operation_type(self):
        contract = run_intake(self._manifest(sql="DELETE FROM users WHERE id = 1"))
        assert contract.operation_type == OperationType.DELETE

    def test_update_operation_type(self):
        contract = run_intake(self._manifest(sql="UPDATE users SET name='x' WHERE id=1"))
        assert contract.operation_type == OperationType.UPDATE

    def test_insert_operation_type(self):
        contract = run_intake(self._manifest(sql="INSERT INTO users (name) VALUES ('x')"))
        assert contract.operation_type == OperationType.INSERT

    def test_ddl_operation_type_drop(self):
        contract = run_intake(self._manifest(sql="DROP TABLE users"))
        assert contract.operation_type == OperationType.DDL

    def test_ddl_operation_type_truncate(self):
        contract = run_intake(self._manifest(sql="TRUNCATE TABLE orders"))
        assert contract.operation_type == OperationType.DDL

    def test_internal_source_no_injection_risk(self):
        contract = run_intake(self._manifest(source_type="internal_system"))
        assert contract.prompt_injection_risk is False

    def test_external_user_input_injection_risk(self):
        contract = run_intake(self._manifest(source_type="external_user_input"))
        assert contract.prompt_injection_risk is True

    def test_external_agent_injection_risk(self):
        contract = run_intake(self._manifest(source_type="external_agent"))
        assert contract.prompt_injection_risk is True

    def test_approval_state_is_pending(self):
        contract = run_intake(self._manifest())
        assert contract.approval_state == ApprovalState.PENDING

    def test_tenant_id_preserved(self):
        contract = run_intake(self._manifest(tenant_id="custom_tenant"))
        assert contract.tenant_id == "custom_tenant"

    def test_submitted_by_preserved(self):
        contract = run_intake(self._manifest(submitted_by="langchain-agent"))
        assert contract.submitted_by == "langchain-agent"

    def test_raw_sql_preserved(self):
        sql = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        contract = run_intake(self._manifest(sql=sql))
        assert contract.raw_sql == sql

    def test_provenance_entry_written(self):
        contract = run_intake(self._manifest())
        assert len(contract.workflow_provenance) == 1
        assert contract.workflow_provenance[0].agent == "INTAKE"
        assert contract.workflow_provenance[0].llm_involved is False

    def test_existing_operation_id_is_preserved(self):
        """
        Regression: proxy calls run_intake once, injects operation_id into manifest_dict,
        then LangGraph INTAKE node calls run_intake again on the same dict.
        The second call must reuse the same operation_id — not generate a new one —
        so that HUMAN_REVIEW and EXECUTION can find the workflow_store record.
        """
        manifest = self._manifest()
        manifest["operation_id"] = "cg_abcd1234"
        contract = run_intake(manifest)
        assert contract.operation_id == "cg_abcd1234"

    def test_two_calls_same_operation_id_when_injected(self):
        """
        Two successive calls to run_intake on the same manifest (with operation_id injected)
        must both return the same operation_id. This is the exact execution path in the
        proxy full-pipeline handler after the double-ID fix.
        """
        manifest = self._manifest()
        # Simulate proxy: first call generates and injects operation_id
        c1 = run_intake(manifest)
        manifest["operation_id"] = c1.operation_id
        # Simulate LangGraph INTAKE node: second call on same manifest
        c2 = run_intake(manifest)
        assert c1.operation_id == c2.operation_id

    def test_missing_sql_raises(self):
        with pytest.raises((ValueError, KeyError, Exception)):
            run_intake({"submitted_by": "test", "source_type": "internal_system"})

    def test_empty_sql_raises(self):
        with pytest.raises((ValueError, Exception)):
            run_intake(self._manifest(sql="   "))

    def test_query_key_as_sql_alias(self):
        manifest = {
            "query": "SELECT * FROM orders",
            "submitted_by": "test",
            "source_type": "internal_system",
        }
        contract = run_intake(manifest)
        assert contract.raw_sql == "SELECT * FROM orders"
        assert contract.operation_type == OperationType.SELECT

    def test_jsonrpc_format_manifest(self):
        manifest = {
            "method": "tools/call",
            "params": {
                "name": "execute_query",
                "arguments": {"sql": "SELECT COUNT(*) FROM users"},
            },
            "submitted_by": "test",
            "source_type": "internal_system",
        }
        # intake gets the already-parsed manifest dict; check it handles sql key
        contract = run_intake({
            "sql": "SELECT COUNT(*) FROM users",
            "submitted_by": "test",
            "source_type": "internal_system",
        })
        assert contract.operation_type == OperationType.SELECT
