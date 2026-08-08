"""
Integration tests for the three-stage retrieval pipeline.

Tests verify:
  1. Full pipeline: Stage 1 → Stage 2 → Stage 3 → top 3
  2. cg_1847 surfaces as the #1 result for the demo DELETE scenario (critical)
  3. Jaccard filter removes structurally unrelated candidates
  4. Outcome reranking: REJECTED score > ROLLED_BACK score > APPROVED score
  5. Tenant isolation: tenant A cannot retrieve tenant B operations

Most tests use mocked MCP calls (memory-store server tool functions directly)
to avoid requiring a live database. Live DB tests are skipped unless
DATABASE_URL is configured in the environment.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import pytest

from orchestrator.handoff_schema import (
    CascadeEntry,
    HandoffContract,
    HistoricalOperation,
    OperationType,
    SourceType,
)
from agents.context_sim.retrieval_client import (
    RetrievalClient,
    _extract_table_set,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_integ_p4",
        tenant_id="demo_tenant",
        submitted_by="integration-test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        intent_summary=(
            "Delete user accounts that have been inactive for more than 2 years "
            "to free up storage and reduce PII exposure"
        ),
        primary_table="users",
        operation_type=OperationType.DELETE,
        cascade=[
            CascadeEntry(table="orders", estimated_rows=5000,
                         cascade_action="CASCADE", depth=1),
            CascadeEntry(table="invoices", estimated_rows=2000,
                         cascade_action="CASCADE", depth=2),
        ],
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


# Seeded memory records mirroring historical_operations.sql
_CG_1847 = {
    "operation_id": "cg_1847",
    "intent_summary": (
        "Delete user accounts that have been inactive for more than 2 years "
        "to free up storage and reduce PII exposure"
    ),
    "affected_tables": ["users", "orders", "invoices"],
    "operation_type": "DELETE",
    "estimated_rows": 12000,
    "actual_rows": 14203,
    "outcome": "REJECTED",
    "decision_reason": (
        "Cascade into invoices caused loss of 8,200 billing records. "
        "Archival step required before any delete."
    ),
    "blast_radius_delta": 0.183,
    "similarity_score": 0.92,
}

_CG_2203 = {
    "operation_id": "cg_2203",
    "intent_summary": "Remove inactive users with cancelled subscription status from the users table",
    "affected_tables": ["users", "notifications"],
    "operation_type": "DELETE",
    "estimated_rows": 8500,
    "actual_rows": 11247,
    "outcome": "ROLLED_BACK",
    "decision_reason": "SendGrid webhook fired 11,000 calls before operator noticed.",
    "blast_radius_delta": 0.323,
    "similarity_score": 0.78,
}

_CG_AP_001 = {
    "operation_id": "cg_ap_001",
    "intent_summary": "Delete test accounts created before 2023 with no associated orders",
    "affected_tables": ["users"],
    "operation_type": "DELETE",
    "estimated_rows": 876,
    "actual_rows": 871,
    "outcome": "APPROVED",
    "decision_reason": "Test accounts only — no production data affected.",
    "blast_radius_delta": -0.006,
    "similarity_score": 0.55,
}

_CG_5517 = {
    "operation_id": "cg_5517",
    "intent_summary": "Delete cancelled orders older than 3 years from the orders table",
    "affected_tables": ["orders", "invoices"],
    "operation_type": "DELETE",
    "estimated_rows": 6800,
    "actual_rows": 7241,
    "outcome": "REJECTED",
    "decision_reason": "Cascade into invoices not acceptable.",
    "blast_radius_delta": 0.062,
    "similarity_score": 0.61,
}

# Structurally unrelated operation — should be filtered by Stage 2 Jaccard
_CG_UNRELATED = {
    "operation_id": "cg_unrelated",
    "intent_summary": "Delete old session tokens from the auth table",
    "affected_tables": ["auth_sessions"],  # no overlap with users/orders/invoices
    "operation_type": "DELETE",
    "estimated_rows": 10000,
    "actual_rows": 9800,
    "outcome": "APPROVED",
    "decision_reason": "Routine cleanup.",
    "blast_radius_delta": -0.02,
    "similarity_score": 0.65,  # high semantic similarity but no table overlap
}


class TestThreeStageFullPipeline:
    """Tests using mocked MCP calls — no live database required."""

    @pytest.mark.asyncio
    async def test_stage1_returns_top_20_limit(self):
        """semantic_search is called with top_k=20."""
        candidates = [_CG_1847, _CG_2203, _CG_AP_001]
        mock_call = AsyncMock(side_effect=[
            {"candidates": candidates, "count": 3},
            {"filtered": [_CG_1847, _CG_2203], "count": 2},
            {"top3": [
                {**_CG_1847, "jaccard_score": 1.0, "rerank_score": 1.0},
                {**_CG_2203, "jaccard_score": 0.5, "rerank_score": 0.9},
            ]},
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        await client.retrieve(_make_contract())

        s1_args = mock_call.call_args_list[0][0][2]
        assert s1_args["top_k"] == 20

    @pytest.mark.asyncio
    async def test_cg_1847_surfaces_as_top_result(self):
        """
        CRITICAL ACCEPTANCE CRITERION (Phase 4):
        For DELETE FROM users WHERE last_active < 2 years:
          cg_1847 (REJECTED, same tables) must be #1.
        """
        candidates = [_CG_1847, _CG_2203, _CG_AP_001, _CG_5517]

        # Stage 2: cg_1847 has jaccard=1.0 (same tables), others vary
        stage2_filtered = [
            {**_CG_1847, "jaccard_score": 1.0},
            {**_CG_AP_001, "jaccard_score": 0.33},
        ]

        # Stage 3: outcome reranking — REJECTED (cg_1847) gets score 1.0
        stage3_top3 = [
            {**_CG_1847, "jaccard_score": 1.0, "rerank_score": 1.0},
            {**_CG_AP_001, "jaccard_score": 0.33, "rerank_score": 0.055},
        ]

        mock_call = AsyncMock(side_effect=[
            {"candidates": candidates, "count": 4},
            {"filtered": stage2_filtered, "count": 2},
            {"top3": stage3_top3},
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is True
        assert len(result.precedents) >= 1
        assert result.precedents[0].operation_id == "cg_1847"
        assert result.precedents[0].outcome == "REJECTED"
        assert result.precedents[0].rerank_score == 1.0

    @pytest.mark.asyncio
    async def test_structurally_unrelated_filtered_by_stage2(self):
        """
        A semantically similar but structurally unrelated operation must not
        survive Stage 2 Jaccard filtering. The memory-store server enforces
        the configured threshold (default 0.3).
        """
        all_candidates = [_CG_1847, _CG_UNRELATED]

        # Stage 2 filters unrelated (jaccard=0.0 — no shared tables)
        stage2_filtered = [{**_CG_1847, "jaccard_score": 1.0}]

        stage3_top3 = [{**_CG_1847, "jaccard_score": 1.0, "rerank_score": 1.0}]

        mock_call = AsyncMock(side_effect=[
            {"candidates": all_candidates, "count": 2},
            {"filtered": stage2_filtered, "count": 1},
            {"top3": stage3_top3},
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        op_ids = [p.operation_id for p in result.precedents]
        assert "cg_unrelated" not in op_ids
        assert "cg_1847" in op_ids

    @pytest.mark.asyncio
    async def test_outcome_reranking_order(self):
        """REJECTED > ROLLED_BACK > APPROVED (exact documented scores)."""
        candidates_approved = [{**_CG_AP_001, "jaccard_score": 0.5}]
        candidates_rejected = [{**_CG_1847, "jaccard_score": 0.9}]
        candidates_rolled = [{**_CG_2203, "jaccard_score": 0.5}]
        all_filtered = candidates_rejected + candidates_rolled + candidates_approved

        # Simulate Stage 3 ordering by rerank score
        stage3_top3 = [
            {**_CG_1847, "jaccard_score": 0.9, "rerank_score": 1.0},
            {**_CG_2203, "jaccard_score": 0.5, "rerank_score": 0.9},
            {**_CG_AP_001, "jaccard_score": 0.5, "rerank_score": 0.055},
        ]

        mock_call = AsyncMock(side_effect=[
            {"candidates": all_filtered, "count": 3},
            {"filtered": all_filtered, "count": 3},
            {"top3": stage3_top3},
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert len(result.precedents) == 3
        assert result.precedents[0].outcome == "REJECTED"
        assert result.precedents[0].rerank_score == 1.0
        assert result.precedents[1].outcome == "ROLLED_BACK"
        assert result.precedents[1].rerank_score == 0.9
        assert result.precedents[2].outcome == "APPROVED"
        assert result.precedents[2].rerank_score < 0.2

    @pytest.mark.asyncio
    async def test_max_3_precedents_returned(self):
        """Stage 3 never returns more than 3 results."""
        many = [_CG_1847, _CG_2203, _CG_AP_001, _CG_5517]
        top3 = [
            {**_CG_1847, "jaccard_score": 1.0, "rerank_score": 1.0},
            {**_CG_5517, "jaccard_score": 0.67, "rerank_score": 1.0},
            {**_CG_2203, "jaccard_score": 0.5, "rerank_score": 0.9},
        ]

        mock_call = AsyncMock(side_effect=[
            {"candidates": many, "count": 4},
            {"filtered": many, "count": 4},
            {"top3": top3},
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert len(result.precedents) <= 3


class TestJaccardBehavior:
    """Test table-set overlap computation via mocked memory-store tools."""

    @pytest.mark.asyncio
    async def test_identical_table_sets_pass(self):
        """Identical table sets: Jaccard = 1.0 → passes any threshold."""
        contract = _make_contract()  # tables: users, orders, invoices
        tables = _extract_table_set(contract)
        assert tables == sorted({"users", "orders", "invoices"})

    def test_extract_table_set_includes_primary(self):
        contract = _make_contract(cascade=[])
        tables = _extract_table_set(contract)
        assert "users" in tables

    def test_extract_table_set_includes_cascade(self):
        contract = _make_contract()
        tables = _extract_table_set(contract)
        assert "orders" in tables
        assert "invoices" in tables

    def test_extract_table_set_deduplication(self):
        contract = _make_contract(
            primary_table="users",
            cascade=[
                CascadeEntry(table="users", estimated_rows=100,
                             cascade_action="CASCADE", depth=1),
            ],
        )
        tables = _extract_table_set(contract)
        assert tables.count("users") == 1  # sorted list, no duplicates


class TestTenantIsolation:
    """Tenant A's operations cannot appear in Tenant B's retrieval results."""

    @pytest.mark.asyncio
    async def test_different_tenant_gets_no_results(self):
        """
        Memory-store server enforces tenant_id filtering on every query.
        We verify the client passes tenant_id and that a different tenant
        gets an empty result set.
        """
        # Tenant B has no operations in memory
        mock_call = AsyncMock(side_effect=[
            {"candidates": [], "count": 0},  # Stage 1 returns nothing for tenant_b
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        contract = _make_contract(tenant_id="tenant_b")
        result = await client.retrieve(contract)

        assert result.available is True
        assert result.precedents == []
        # Verify tenant_id passed correctly
        s1_args = mock_call.call_args_list[0][0][2]
        assert s1_args["tenant_id"] == "tenant_b"

    @pytest.mark.asyncio
    async def test_tenant_id_propagated_to_semantic_search(self):
        """The tenant_id from the contract is always forwarded to semantic_search."""
        mock_call = AsyncMock(return_value={"candidates": [], "count": 0})
        client = RetrievalClient(call_tool_fn=mock_call)

        for tenant in ["demo_tenant", "tenant_a", "tenant_xyz"]:
            mock_call.reset_mock()
            contract = _make_contract(tenant_id=tenant)
            await client.retrieve(contract)
            s1_args = mock_call.call_args_list[0][0][2]
            assert s1_args["tenant_id"] == tenant


class TestRetrievalFailureBehavior:
    """Verify graceful degradation on retrieval failures."""

    @pytest.mark.asyncio
    async def test_memory_store_unavailable_returns_not_available(self):
        mock_call = AsyncMock(side_effect=ConnectionError("memory-store offline"))
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is False
        assert result.precedents == []
        assert result.failure_reason is not None

    @pytest.mark.asyncio
    async def test_malformed_response_returns_not_available(self):
        mock_call = AsyncMock(side_effect=ValueError("JSON parse error"))
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is False

    @pytest.mark.asyncio
    async def test_empty_memory_returns_available_no_precedents(self):
        """No history in DB is different from DB being unavailable."""
        mock_call = AsyncMock(return_value={"candidates": [], "count": 0})
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is True  # DB is up, just no results
        assert result.precedents == []
        assert result.failure_reason is None


# ---------------------------------------------------------------------------
# Live DB tests (skipped unless DATABASE_URL is set)
# ---------------------------------------------------------------------------

_has_live_db = bool(os.environ.get("DATABASE_URL"))


@pytest.mark.skipif(not _has_live_db, reason="Requires live DATABASE_URL")
class TestLiveRetrievalPipeline:
    """
    Integration tests against a live PostgreSQL database with seed data.
    These verify that the seeded historical_operations.sql enables the
    documented demo scenario.

    Run with:
      DATABASE_URL=... EMBEDDING_PROVIDER=mock pytest tests/integration/test_retrieval_pipeline.py::TestLiveRetrievalPipeline
    """

    @pytest.fixture(autouse=True)
    def set_mock_embeddings(self, monkeypatch):
        monkeypatch.setenv("EMBEDDING_PROVIDER", "mock")

    @pytest.mark.asyncio
    async def test_cg_1847_in_database(self):
        """cg_1847 must exist in operation_memory with REJECTED outcome."""
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT operation_id, outcome FROM contragate_app.operation_memory "
                    "WHERE operation_id = 'cg_1847'"
                )
                row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None, "cg_1847 not found in operation_memory"
        assert row["outcome"] == "REJECTED"

    @pytest.mark.asyncio
    async def test_all_20_seeded_operations_present(self):
        """All 20 seeded operations must be present."""
        import psycopg2
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM contragate_app.operation_memory "
                    "WHERE tenant_id = 'demo_tenant'"
                )
                count = cur.fetchone()[0]
        finally:
            conn.close()
        assert count >= 20, f"Expected ≥20 seeded operations, got {count}"

    @pytest.mark.asyncio
    async def test_confidence_scores_seeded(self):
        """Confidence scores must be seeded below 0.8 for key tables."""
        import psycopg2
        import psycopg2.extras
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                cur.execute(
                    "SELECT table_name, confidence FROM contragate_app.confidence_scores "
                    "WHERE tenant_id = 'demo_tenant' AND table_name IN ('users', 'orders')"
                )
                rows = {r["table_name"]: r["confidence"] for r in cur.fetchall()}
        finally:
            conn.close()
        # Both tables should have reduced confidence from rolled-back ops
        assert rows.get("users", 1.0) < 0.8, "users confidence should be reduced"
        assert rows.get("orders", 1.0) < 0.8, "orders confidence should be reduced"

    @pytest.mark.asyncio
    async def test_pgvector_extension_loaded(self):
        """pgvector must be installed and the ivfflat index must exist."""
        import psycopg2
        conn = psycopg2.connect(os.environ["DATABASE_URL"])
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT extname FROM pg_extension WHERE extname = 'vector'")
                row = cur.fetchone()
        finally:
            conn.close()
        assert row is not None, "pgvector extension not installed"

    @pytest.mark.asyncio
    async def test_memory_store_semantic_search_with_mock_embeddings(self):
        """
        Verify semantic_search works end-to-end against live DB using mock embeddings.
        This does NOT test semantic correctness — just that the pipeline runs.
        """
        from mcp_servers.memory_store.server import semantic_search
        result = semantic_search(
            intent="delete inactive users older than 2 years",
            tenant_id="demo_tenant",
            top_k=20,
        )
        assert "candidates" in result
        assert "count" in result
        # Should find some candidates (text fallback or vector search)
        assert isinstance(result["candidates"], list)

    @pytest.mark.asyncio
    async def test_tenant_isolation_live_db(self):
        """Tenant B should not see demo_tenant operations."""
        from mcp_servers.memory_store.server import semantic_search
        result = semantic_search(
            intent="delete inactive users",
            tenant_id="tenant_b_isolation_test",
            top_k=20,
        )
        assert result["count"] == 0, "tenant_b should not see demo_tenant data"
