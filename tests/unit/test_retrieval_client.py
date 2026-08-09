"""
Unit tests for agents/context_sim/retrieval_client.py.

All tests use mocked call_tool — no MCP server connections.
Tests verify:
  - Full three-stage pipeline success
  - Stage 1 empty → immediate return
  - Stage 2 filters all → no precedents
  - Table extraction from contract
  - MCP failure → available=False, no raise
  - HistoricalOperation field mapping
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, call

from orchestrator.handoff_schema import (
    CascadeEntry,
    HandoffContract,
    HistoricalOperation,
    OperationType,
    SourceType,
)
from agents.context_sim.retrieval_client import RetrievalClient, _extract_table_set


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_test_p4",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        intent_summary="Delete inactive user accounts older than 2 years",
        primary_table="users",
        cascade=[
            CascadeEntry(table="orders", estimated_rows=5000,
                         cascade_action="CASCADE", depth=1),
            CascadeEntry(table="invoices", estimated_rows=2000,
                         cascade_action="CASCADE", depth=2),
        ],
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


_SEEDED_REJECTED = {
    "operation_id": "cg_1847",
    "intent_summary": "Delete inactive user accounts older than 2 years",
    "affected_tables": ["users", "orders", "invoices"],
    "operation_type": "DELETE",
    "estimated_rows": 12000,
    "actual_rows": 14203,
    "outcome": "REJECTED",
    "decision_reason": "Cascade into invoices caused loss of 8,200 billing records.",
    "blast_radius_delta": 0.183,
    "similarity_score": 0.91,
}

_SEEDED_ROLLED_BACK = {
    "operation_id": "cg_2203",
    "intent_summary": "Remove inactive users with cancelled subscription status",
    "affected_tables": ["users", "notifications"],
    "operation_type": "DELETE",
    "estimated_rows": 8500,
    "actual_rows": 11247,
    "outcome": "ROLLED_BACK",
    "decision_reason": "SendGrid webhook fired 11,000 calls.",
    "blast_radius_delta": 0.323,
    "similarity_score": 0.72,
}


def _make_stage1_result(candidates):
    return {"candidates": candidates, "count": len(candidates)}


def _make_stage2_result(filtered):
    for c in filtered:
        if "jaccard_score" not in c:
            c["jaccard_score"] = 0.67
    return {"filtered": filtered, "count": len(filtered)}


def _make_stage3_result(top3):
    for c in top3:
        if "rerank_score" not in c:
            c["rerank_score"] = 1.0 if c.get("outcome") == "REJECTED" else 0.9
    return {"top3": top3}


class TestRetrievalClientSuccess:
    @pytest.mark.asyncio
    async def test_full_pipeline_returns_precedents(self):
        candidates = [_SEEDED_REJECTED, _SEEDED_ROLLED_BACK]
        mock_call = AsyncMock(side_effect=[
            _make_stage1_result(candidates),
            _make_stage2_result(candidates),
            _make_stage3_result([{**_SEEDED_REJECTED, "rerank_score": 1.0},
                                  {**_SEEDED_ROLLED_BACK, "rerank_score": 0.9}]),
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is True
        assert result.stage1_candidates == 2
        assert result.stage2_candidates == 2
        assert len(result.precedents) == 2
        assert result.failure_reason is None

    @pytest.mark.asyncio
    async def test_rejected_precedent_correctly_mapped(self):
        candidates = [{**_SEEDED_REJECTED, "jaccard_score": 0.75, "rerank_score": 1.0}]
        mock_call = AsyncMock(side_effect=[
            _make_stage1_result(candidates),
            _make_stage2_result(candidates),
            _make_stage3_result(candidates),
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        prec = result.precedents[0]
        assert isinstance(prec, HistoricalOperation)
        assert prec.operation_id == "cg_1847"
        assert prec.outcome == "REJECTED"
        assert prec.similarity_score == 0.91
        assert prec.jaccard_score == 0.75
        assert prec.rerank_score == 1.0
        assert "users" in prec.tables

    @pytest.mark.asyncio
    async def test_three_mcp_calls_made(self):
        candidates = [_SEEDED_REJECTED]
        mock_call = AsyncMock(side_effect=[
            _make_stage1_result(candidates),
            _make_stage2_result(candidates),
            _make_stage3_result([{**_SEEDED_REJECTED, "rerank_score": 1.0}]),
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        await client.retrieve(_make_contract())

        assert mock_call.call_count == 3
        calls = mock_call.call_args_list
        assert calls[0][0][1] == "semantic_search"
        assert calls[1][0][1] == "filter_by_table_overlap"
        assert calls[2][0][1] == "rerank_by_outcome"

    @pytest.mark.asyncio
    async def test_tenant_id_passed_to_semantic_search(self):
        mock_call = AsyncMock(side_effect=[
            _make_stage1_result([_SEEDED_REJECTED]),
            _make_stage2_result([_SEEDED_REJECTED]),
            _make_stage3_result([{**_SEEDED_REJECTED, "rerank_score": 1.0}]),
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        contract = _make_contract(tenant_id="tenant_xyz")
        await client.retrieve(contract)

        s1_args = mock_call.call_args_list[0][0][2]
        assert s1_args["tenant_id"] == "tenant_xyz"

    @pytest.mark.asyncio
    async def test_top_k_20_requested(self):
        mock_call = AsyncMock(side_effect=[
            _make_stage1_result([]),
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        await client.retrieve(_make_contract())

        s1_args = mock_call.call_args_list[0][0][2]
        assert s1_args["top_k"] == 20


class TestRetrievalClientEmptyStages:
    @pytest.mark.asyncio
    async def test_empty_stage1_returns_no_precedents(self):
        mock_call = AsyncMock(return_value={"candidates": [], "count": 0})
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is True
        assert result.stage1_candidates == 0
        assert result.precedents == []
        # Only stage 1 is called when empty
        assert mock_call.call_count == 1

    @pytest.mark.asyncio
    async def test_stage2_filters_all_returns_empty(self):
        mock_call = AsyncMock(side_effect=[
            _make_stage1_result([_SEEDED_REJECTED]),
            {"filtered": [], "count": 0},
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is True
        assert result.stage1_candidates == 1
        assert result.stage2_candidates == 0
        assert result.precedents == []
        assert mock_call.call_count == 2  # Stage 3 not called

    @pytest.mark.asyncio
    async def test_no_intent_uses_raw_sql_prefix(self):
        mock_call = AsyncMock(return_value={"candidates": [], "count": 0})
        client = RetrievalClient(call_tool_fn=mock_call)
        contract = _make_contract(intent_summary="")  # No intent
        await client.retrieve(contract)

        s1_args = mock_call.call_args_list[0][0][2]
        # Should use raw_sql prefix as fallback
        assert len(s1_args["intent"]) > 0
        assert "DELETE" in s1_args["intent"].upper()


class TestRetrievalClientFailure:
    @pytest.mark.asyncio
    async def test_mcp_failure_returns_unavailable(self):
        mock_call = AsyncMock(side_effect=RuntimeError("memory-store unreachable"))
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is False
        assert result.precedents == []
        assert result.failure_reason is not None
        assert "memory-store unreachable" in result.failure_reason

    @pytest.mark.asyncio
    async def test_stage2_failure_returns_unavailable(self):
        mock_call = AsyncMock(side_effect=[
            _make_stage1_result([_SEEDED_REJECTED]),
            RuntimeError("connection reset"),
        ])
        client = RetrievalClient(call_tool_fn=mock_call)
        result = await client.retrieve(_make_contract())

        assert result.available is False
        assert result.precedents == []


class TestTableExtraction:
    def test_extracts_primary_table(self):
        contract = _make_contract(cascade=[])
        tables = _extract_table_set(contract)
        assert "users" in tables

    def test_extracts_cascade_tables(self):
        contract = _make_contract()
        tables = _extract_table_set(contract)
        assert "users" in tables
        assert "orders" in tables
        assert "invoices" in tables

    def test_no_primary_table(self):
        contract = _make_contract(primary_table="")
        tables = _extract_table_set(contract)
        assert "orders" in tables
        assert "" not in tables


class TestApplyToContract:
    def test_applies_available_result(self):
        from agents.context_sim.retrieval_client import RetrievalResult
        prec = HistoricalOperation(
            operation_id="cg_1847", intent_summary="delete users",
            tables=["users"], outcome="REJECTED", decision_reason="test",
            similarity_score=0.9, jaccard_score=0.7, rerank_score=1.0,
        )
        result = RetrievalResult(
            precedents=[prec], stage1_candidates=5,
            stage2_candidates=2, available=True,
        )
        contract = _make_contract()
        client = RetrievalClient(call_tool_fn=AsyncMock())
        client.apply_to_contract(contract, result)

        assert contract.retrieval_available is True
        assert len(contract.historical_precedents) == 1
        assert contract.historical_precedents[0].operation_id == "cg_1847"

    def test_applies_unavailable_result(self):
        from agents.context_sim.retrieval_client import RetrievalResult
        result = RetrievalResult(
            precedents=[], stage1_candidates=0,
            stage2_candidates=0, available=False,
            failure_reason="DB down",
        )
        contract = _make_contract()
        client = RetrievalClient(call_tool_fn=AsyncMock())
        client.apply_to_contract(contract, result)

        assert contract.retrieval_available is False
        assert contract.historical_precedents == []
