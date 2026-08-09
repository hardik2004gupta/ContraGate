"""
Phase 6 — test_manifest_binding.py

Verifies original manifest persistence and identity binding:
- workflow_store stores the original tool-call manifest keyed by approval_id
- The manifest is retrieved intact (not modified) in execution
- Manifest includes the SQL, tool_name, and target_mcp_url
- Hash stored in manifest matches recomputed hash of the manifest body
"""

from __future__ import annotations

import hashlib
import json

import pytest

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    SourceType,
)
from orchestrator.workflow_store import WorkflowStore
from orchestrator.states.execution import _manifest_hash


def _make_contract(op_id: str = "cg_bind00001") -> HandoffContract:
    return HandoffContract(
        operation_id=op_id,
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM orders WHERE status = 'cancelled'",
        operation_type=OperationType.DELETE,
        approval_state=ApprovalState.APPROVED,
    )


def _build_manifest(sql: str) -> dict:
    """Build a manifest as proxy/main.py would — with _manifest_hash stored inside."""
    body = {
        "tool_name": "execute_query",
        "sql": sql,
        "tenant_id": "demo_tenant",
        "target_mcp_url": "http://contragate-mcp:8010",
    }
    body["_manifest_hash"] = _manifest_hash(body.copy())
    return body


@pytest.mark.asyncio
class TestManifestStorage:
    async def test_manifest_stored_on_create(self):
        store = WorkflowStore()
        contract = _make_contract()
        manifest = _build_manifest(contract.raw_sql)
        record = await store.create(contract, manifest)
        assert record.tool_call_manifest is not None

    async def test_manifest_retrieved_intact(self):
        store = WorkflowStore()
        contract = _make_contract()
        manifest = _build_manifest(contract.raw_sql)
        await store.create(contract, manifest)

        record = await store.get(contract.operation_id)
        assert record.tool_call_manifest == manifest

    async def test_manifest_contains_original_sql(self):
        store = WorkflowStore()
        contract = _make_contract()
        manifest = _build_manifest(contract.raw_sql)
        await store.create(contract, manifest)

        record = await store.get(contract.operation_id)
        assert record.tool_call_manifest["sql"] == contract.raw_sql

    async def test_manifest_contains_tool_name(self):
        store = WorkflowStore()
        contract = _make_contract()
        manifest = _build_manifest(contract.raw_sql)
        await store.create(contract, manifest)

        record = await store.get(contract.operation_id)
        assert "tool_name" in record.tool_call_manifest

    async def test_manifest_not_overwritten_by_update_contract(self):
        store = WorkflowStore()
        contract = _make_contract()
        manifest = _build_manifest(contract.raw_sql)
        await store.create(contract, manifest)

        # Update the contract (e.g., after analysis adds blast radius info)
        contract.estimated_primary_rows = 5000
        await store.update_contract(contract.operation_id, contract)

        record = await store.get(contract.operation_id)
        # Manifest should remain unchanged
        assert record.tool_call_manifest["sql"] == contract.raw_sql
        assert record.tool_call_manifest["_manifest_hash"] == manifest["_manifest_hash"]

    async def test_different_operations_have_separate_manifests(self):
        store = WorkflowStore()
        c1 = _make_contract("cg_bind00001")
        c2 = _make_contract("cg_bind00002")

        sql1 = "DELETE FROM users WHERE id = 1"
        sql2 = "DELETE FROM orders WHERE id = 2"

        await store.create(c1, _build_manifest(sql1))
        await store.create(c2, _build_manifest(sql2))

        r1 = await store.get(c1.operation_id)
        r2 = await store.get(c2.operation_id)

        assert r1.tool_call_manifest["sql"] == sql1
        assert r2.tool_call_manifest["sql"] == sql2
        assert r1.tool_call_manifest["_manifest_hash"] != r2.tool_call_manifest["_manifest_hash"]


@pytest.mark.asyncio
class TestManifestHashBinding:
    async def test_stored_hash_matches_recomputed_hash(self):
        """The _manifest_hash stored at proxy time must match recomputed hash at execution time."""
        sql = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        manifest = {
            "tool_name": "execute_query",
            "sql": sql,
            "tenant_id": "demo_tenant",
            "target_mcp_url": "http://contragate-mcp:8010",
        }
        # Compute hash of body (without _manifest_hash key)
        body = {k: v for k, v in manifest.items()}
        stored_hash = _manifest_hash(body)
        manifest["_manifest_hash"] = stored_hash

        # Simulate what execution.py does: recompute hash from manifest minus _manifest_hash
        body_at_execution = {k: v for k, v in manifest.items() if k != "_manifest_hash"}
        recomputed_hash = _manifest_hash(body_at_execution)

        assert stored_hash == recomputed_hash

    async def test_tampered_sql_invalidates_hash(self):
        """Changing the SQL after storing the hash causes a mismatch."""
        manifest = {
            "tool_name": "execute_query",
            "sql": "DELETE FROM users WHERE id = 1",
            "tenant_id": "demo_tenant",
        }
        body = {k: v for k, v in manifest.items()}
        manifest["_manifest_hash"] = _manifest_hash(body)

        # Tamper: change SQL after hash computed
        manifest["sql"] = "TRUNCATE users"

        body_at_execution = {k: v for k, v in manifest.items() if k != "_manifest_hash"}
        recomputed = _manifest_hash(body_at_execution)

        assert manifest["_manifest_hash"] != recomputed

    async def test_manifest_hash_bound_to_approval_id(self):
        """Each workflow record has exactly one manifest, keyed by approval_id."""
        store = WorkflowStore()
        contract = _make_contract("cg_bind00003")
        manifest = _build_manifest(contract.raw_sql)

        record = await store.create(contract, manifest)
        assert record.approval_id == contract.operation_id

        retrieved = await store.get(record.approval_id)
        assert retrieved.tool_call_manifest["_manifest_hash"] == manifest["_manifest_hash"]
