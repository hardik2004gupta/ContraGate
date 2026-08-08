"""
Phase 6 — test_stale_contract.py

Verifies the stale-state guard:
- _manifest_hash() produces consistent deterministic hashes
- Manifest hash is stored inside the manifest dict as '_manifest_hash'
- If stored hash doesn't match recomputed hash, execution is aborted
- If stored hash matches, execution proceeds normally
- Hash stability: same manifest → same hash
"""

from __future__ import annotations

import hashlib
import json

import pytest

from orchestrator.states.execution import _manifest_hash


# ── _manifest_hash pure function ──────────────────────────────────────────────

class TestManifestHashPureFunction:
    def test_same_manifest_same_hash(self):
        manifest = {"tool_name": "execute_query", "sql": "DELETE FROM users WHERE id = 1"}
        assert _manifest_hash(manifest) == _manifest_hash(manifest)

    def test_different_sql_different_hash(self):
        m1 = {"tool_name": "execute_query", "sql": "DELETE FROM users WHERE id = 1"}
        m2 = {"tool_name": "execute_query", "sql": "DELETE FROM users WHERE id = 2"}
        assert _manifest_hash(m1) != _manifest_hash(m2)

    def test_hash_is_sha256_hex(self):
        manifest = {"sql": "SELECT 1"}
        h = _manifest_hash(manifest)
        assert len(h) == 64
        assert all(c in "0123456789abcdef" for c in h)

    def test_key_order_independent(self):
        m1 = {"tool_name": "run_query", "sql": "SELECT 1", "tenant_id": "demo_tenant"}
        m2 = {"sql": "SELECT 1", "tenant_id": "demo_tenant", "tool_name": "run_query"}
        # sort_keys=True ensures same hash regardless of insertion order
        assert _manifest_hash(m1) == _manifest_hash(m2)

    def test_extra_field_changes_hash(self):
        m1 = {"sql": "SELECT 1"}
        m2 = {"sql": "SELECT 1", "extra": "field"}
        assert _manifest_hash(m1) != _manifest_hash(m2)


# ── Stale-state guard in execution.py ────────────────────────────────────────

@pytest.mark.asyncio
class TestStaleContractGuard:
    async def _make_store_with_manifest(self, manifest: dict):
        from orchestrator.handoff_schema import (
            ApprovalState, HandoffContract, OperationType, SourceType
        )
        from orchestrator.workflow_store import WorkflowStore

        contract = HandoffContract(
            operation_id="cg_stale0001",
            tenant_id="demo_tenant",
            submitted_by="test",
            source_type=SourceType.INTERNAL_SYSTEM,
            raw_sql=manifest.get("sql", "SELECT 1"),
            operation_type=OperationType.DELETE,
            approval_state=ApprovalState.APPROVED,
        )
        store = WorkflowStore()
        await store.create(contract, manifest)
        return store, contract

    async def test_matching_hash_allows_execution(self):
        from unittest.mock import patch

        sql = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        manifest = {"tool_name": "execute_query", "sql": sql}
        # Store the hash inside the manifest (same as proxy/main.py does)
        manifest["_manifest_hash"] = _manifest_hash(
            {k: v for k, v in manifest.items() if k != "_manifest_hash"}
        )

        store, contract = await self._make_store_with_manifest(manifest)

        call_count = {"n": 0}

        async def mock_call(*args, **kwargs):
            call_count["n"] += 1
            return {"rows_deleted": 0}

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=mock_call),
        ):
            from orchestrator.states.execution import run_execution
            result = await run_execution(contract)

        assert call_count["n"] == 1, "Execution should have proceeded with matching hash"
        assert result.execution_success is True

    async def test_mismatched_hash_blocks_execution(self):
        from unittest.mock import patch

        sql = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        manifest = {"tool_name": "execute_query", "sql": sql}
        # Store a WRONG hash to simulate a tampered manifest
        manifest["_manifest_hash"] = "deadbeef" * 8  # wrong 64-char hex

        store, contract = await self._make_store_with_manifest(manifest)

        call_count = {"n": 0}

        async def mock_call(*args, **kwargs):
            call_count["n"] += 1
            return {"rows_deleted": 0}

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=mock_call),
        ):
            from orchestrator.states.execution import run_execution
            result = await run_execution(contract)

        assert call_count["n"] == 0, "Execution must be blocked on hash mismatch"
        assert result.execution_success is False

    async def test_no_hash_in_manifest_skips_check(self):
        """If manifest has no _manifest_hash key, stale-state guard is skipped."""
        from unittest.mock import patch

        sql = "SELECT 1"
        manifest = {"tool_name": "execute_query", "sql": sql}
        # Intentionally NOT adding _manifest_hash

        from orchestrator.handoff_schema import (
            ApprovalState, HandoffContract, OperationType, SourceType
        )
        from orchestrator.workflow_store import WorkflowStore

        contract = HandoffContract(
            operation_id="cg_nohash001",
            tenant_id="demo_tenant",
            submitted_by="test",
            source_type=SourceType.INTERNAL_SYSTEM,
            raw_sql=sql,
            operation_type=OperationType.SELECT,
            approval_state=ApprovalState.APPROVED,
        )
        store = WorkflowStore()
        await store.create(contract, manifest)

        call_count = {"n": 0}

        async def mock_call(*args, **kwargs):
            call_count["n"] += 1
            return {"rows": []}

        with (
            patch("orchestrator.workflow_store.workflow_store", store),
            patch("orchestrator.states.execution.mcp_client.call", new=mock_call),
        ):
            from orchestrator.states.execution import run_execution
            result = await run_execution(contract)

        # Without a stored hash, the guard is skipped and execution proceeds
        assert call_count["n"] == 1
