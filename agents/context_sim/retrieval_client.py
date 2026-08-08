"""
Retrieval client — typed adapter around the memory-store MCP server.

Calls the three-stage retrieval pipeline through the MCP boundary:
  Stage 1: semantic_search    → top 20 candidates by cosine similarity
  Stage 2: filter_by_table_overlap → Jaccard ≥ configured threshold
  Stage 3: rerank_by_outcome  → top 3 by outcome severity score

The memory-store MCP server owns the algorithms (embeddings, Jaccard, ranking).
This client owns: typed calls, validation, error handling, contract integration.

No LLM calls. No direct database access. All access via BaseAgent.call_tool().
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from orchestrator.handoff_schema import HandoffContract, HistoricalOperation

logger = logging.getLogger(__name__)

_CONTEXT_FIELDS = (
    "retrieval_available,historical_precedents"
)


@dataclass(frozen=True)
class RetrievalResult:
    """Typed result from the three-stage retrieval pipeline."""
    precedents: list[HistoricalOperation]
    stage1_candidates: int
    stage2_candidates: int
    available: bool
    failure_reason: str | None = None


def _extract_table_set(contract: HandoffContract) -> list[str]:
    tables = set()
    if contract.primary_table:
        tables.add(contract.primary_table)
    for entry in contract.cascade:
        tables.add(entry.table)
    return sorted(tables)


def _to_historical_operation(raw: dict) -> HistoricalOperation:
    return HistoricalOperation(
        operation_id=raw.get("operation_id", "unknown"),
        intent_summary=raw.get("intent_summary", ""),
        tables=list(raw.get("affected_tables", raw.get("tables", []))),
        outcome=raw.get("outcome", "UNKNOWN"),
        decision_reason=raw.get("decision_reason", ""),
        similarity_score=float(raw.get("similarity_score", 0.0)),
        jaccard_score=float(raw.get("jaccard_score", 0.0)),
        rerank_score=float(raw.get("rerank_score", 0.0)),
    )


class RetrievalClient:
    """
    Thin typed adapter for three-stage historical retrieval via memory-store MCP.

    Instantiated per-operation. All calls go through call_tool for audit logging.
    """

    def __init__(self, call_tool_fn) -> None:
        self._call_tool = call_tool_fn

    async def retrieve(self, contract: HandoffContract) -> RetrievalResult:
        """
        Run the three-stage retrieval pipeline.

        Returns RetrievalResult. Never raises — failures return available=False.
        """
        intent = contract.intent_summary or contract.raw_sql[:200]

        try:
            # ── Stage 1: Semantic search — top 20 candidates ─────────────────
            stage1 = await self._call_tool(
                "memory-store",
                "semantic_search",
                {
                    "intent": intent,
                    "tenant_id": contract.tenant_id,
                    "top_k": 20,
                },
            )
            candidates = stage1.get("candidates", [])
            stage1_count = len(candidates)
            logger.debug(
                "Stage 1: %d candidates for operation %s",
                stage1_count, contract.operation_id,
            )

            if not candidates:
                return RetrievalResult(
                    precedents=[],
                    stage1_candidates=0,
                    stage2_candidates=0,
                    available=True,
                )

            # ── Stage 2: Structural filter by table overlap ───────────────────
            current_tables = _extract_table_set(contract)
            stage2 = await self._call_tool(
                "memory-store",
                "filter_by_table_overlap",
                {
                    "candidates": candidates,
                    "current_tables": current_tables,
                },
            )
            filtered = stage2.get("filtered", [])
            stage2_count = len(filtered)
            logger.debug(
                "Stage 2: %d/%d candidates pass Jaccard filter",
                stage2_count, stage1_count,
            )

            if not filtered:
                return RetrievalResult(
                    precedents=[],
                    stage1_candidates=stage1_count,
                    stage2_candidates=0,
                    available=True,
                )

            # ── Stage 3: Outcome-aware reranking — top 3 ─────────────────────
            stage3 = await self._call_tool(
                "memory-store",
                "rerank_by_outcome",
                {"candidates": filtered},
            )
            ranked = stage3.get("top3", [])
            logger.debug(
                "Stage 3: returning top %d of %d filtered candidates",
                len(ranked), stage2_count,
            )

            precedents = [_to_historical_operation(r) for r in ranked]
            return RetrievalResult(
                precedents=precedents,
                stage1_candidates=stage1_count,
                stage2_candidates=stage2_count,
                available=True,
            )

        except Exception as exc:
            logger.warning(
                "Retrieval failed for operation %s: %s",
                contract.operation_id, exc,
            )
            return RetrievalResult(
                precedents=[],
                stage1_candidates=0,
                stage2_candidates=0,
                available=False,
                failure_reason=str(exc),
            )

    def apply_to_contract(
        self, contract: HandoffContract, result: RetrievalResult
    ) -> None:
        """Write retrieval results into the HandoffContract."""
        contract.retrieval_available = result.available
        contract.historical_precedents = result.precedents
