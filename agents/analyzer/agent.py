"""
Analyzer Agent — Phase 3 implementation.

Converts an intercepted MCP tool call into a structured consequence map:
  - operation_type (deterministic, pure SQL parsing)
  - primary_table, condition (regex extraction)
  - estimated_primary_rows, row_confidence (postgres-reader MCP)
  - cascade (FK graph from postgres-reader MCP)
  - external_triggers (trigger detection from postgres-reader MCP)
  - reversibility, reversibility_reason (sql_analysis_lib — deterministic, no LLM)
  - permanent_components (derived from reversibility)
  - intent_summary (LLM structured output — the ONLY LLM call)

Security invariants (CLAUDE.md §22):
  - Invariant 2: Risk tier is NEVER produced by this agent (Contract Agent only)
  - Invariant 3: No LLM-generated rollback SQL — automated_recovery_sql is always None
  - Invariant 11: raw_sql is wrapped in <untrusted_input> before LLM processing
  - Invariant 14: provenance appended on every field write
  - All external data access goes through BaseAgent.call_tool() (no direct DB)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from agents.base_agent import BaseAgent, wrap_untrusted
from agents.analyzer.prompts import (
    INTENT_SUMMARY_TOOL,
    INTENT_SUMMARY_TOOL_NAME,
    build_intent_prompt,
)
from agents.analyzer.tools import (
    build_cascade_entries,
    build_external_triggers,
    build_trigger_analysis_from_mcp,
    classify_operation_type,
    estimate_api_fanout,
    identify_permanent_components,
    parse_sql_intent,
)
from mcp_servers.audit_logger.checksum import hash_payload
from orchestrator.guards import needs_selective_reanalysis
from orchestrator.handoff_schema import (
    HandoffContract,
    OperationType,
    ReversibilityClass,
)
from sql_analysis_lib.reversibility_rules import (
    ReversibilityClass as LibReversibilityClass,
    classify_reversibility,
)

logger = logging.getLogger(__name__)

_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")

_ANALYSIS_FIELDS = frozenset({
    "intent_summary",
    "operation_type",
    "primary_table",
    "condition",
    "estimated_primary_rows",
    "row_confidence",
    "cascade",
    "external_triggers",
    "reversibility",
    "reversibility_reason",
    "permanent_components",
})


class AnalyzerAgent(BaseAgent):
    """
    Agent 1: Analyzer — blast radius, reversibility, and intent summarization.

    The only LLM call is _get_intent_summary(), which uses structured output
    (tool_use) to produce a single human-readable sentence. All other outputs
    are deterministic.
    """

    AGENT_NAME = "ANALYZER_AGENT"

    async def analyze(self, contract: HandoffContract) -> HandoffContract:
        """
        Run full analysis on the HandoffContract and return the updated contract.
        Selective re-analysis: if contract.stale_fields is set, only re-run
        fields listed there; all other fields are preserved as-is.
        """
        start = datetime.now(timezone.utc)

        # ── Selective re-analysis guard ─────────────────────────────────────
        if needs_selective_reanalysis(contract):
            stale = set(contract.stale_fields)
            if not (_ANALYSIS_FIELDS & stale):
                logger.info(
                    "ANALYZER skipped — all analysis fields fresh",
                    extra={"operation_id": contract.operation_id},
                )
                return contract

        # ── Step 1: Classify operation type (pure) ──────────────────────────
        op_type = classify_operation_type(contract.raw_sql)
        contract.operation_type = op_type

        # ── Step 2: Parse primary table and WHERE condition (pure) ──────────
        primary_table, condition = parse_sql_intent(contract.raw_sql)
        if primary_table:
            contract.primary_table = primary_table
        if condition:
            contract.condition = condition

        # ── Step 3: Count affected rows (postgres-reader MCP) ───────────────
        row_result = await self._count_affected_rows(
            contract.primary_table, contract.condition
        )
        contract.estimated_primary_rows = row_result.get("estimated_rows", 0)
        contract.row_confidence = row_result.get("confidence", 0.5)

        # ── Step 4: Detect trigger side effects (postgres-reader MCP) ───────
        trigger_result: dict = {}
        if contract.primary_table:
            trigger_result = await self._detect_triggers(contract.primary_table)
            external = build_external_triggers(trigger_result)
            contract.external_triggers = estimate_api_fanout(
                external, contract.estimated_primary_rows
            )

        # ── Step 5: Trace FK cascades (postgres-reader MCP, DELETE/UPDATE) ──
        fk_result: dict = {}
        if contract.primary_table and op_type in (
            OperationType.DELETE, OperationType.UPDATE
        ):
            fk_result = await self._trace_fk_graph(contract.primary_table)
            contract.cascade = build_cascade_entries(
                fk_result, contract.estimated_primary_rows
            )

        # ── Step 6: Check soft-delete column (postgres-reader MCP) ──────────
        has_soft_delete = False
        if contract.primary_table and op_type in (
            OperationType.DELETE, OperationType.UPDATE
        ):
            sd_result = await self._check_soft_delete(contract.primary_table)
            has_soft_delete = sd_result.get("has_soft_delete", False)

        # ── Step 7: Classify reversibility (sql_analysis_lib — deterministic) ─
        trigger_analysis = build_trigger_analysis_from_mcp(
            contract.primary_table or "", trigger_result
        )
        try:
            rev_result = classify_reversibility(
                operation_type=op_type.value,
                table=contract.primary_table or "",
                has_soft_delete_column=has_soft_delete,
                trigger_analysis=trigger_analysis,
                pitr_confirmed=False,   # MVP: no PITR confirmation
                pitr_window_hours=None,
                estimated_rows=contract.estimated_primary_rows,
            )
            contract.reversibility = ReversibilityClass(
                rev_result.classification.value
            )
            contract.reversibility_reason = rev_result.reason
            contract.automated_recovery_sql = None  # ADR-008: prohibited
            contract.permanent_components = rev_result.permanent_components
        except ValueError as exc:
            logger.warning(
                "Reversibility classification failed, defaulting to PERMANENT: %s",
                exc,
                extra={"operation_id": contract.operation_id},
            )
            contract.reversibility = ReversibilityClass.PERMANENT
            contract.reversibility_reason = f"Classification error — defaulting to PERMANENT: {exc}"
            contract.permanent_components = []

        # Re-derive permanent_components list (handles PARTIAL case too)
        contract.permanent_components = identify_permanent_components(
            reversibility=contract.reversibility,
            operation_type=op_type,
            primary_table=contract.primary_table,
            external_triggers=contract.external_triggers,
            cascade=contract.cascade,
        )

        # ── Step 8: Intent summary from LLM (structured output only) ────────
        contract.intent_summary = await self._get_intent_summary(contract)

        # ── Step 9: Provenance ───────────────────────────────────────────────
        contract.add_provenance(
            agent=self.AGENT_NAME,
            field_written=(
                "operation_type,primary_table,condition,estimated_primary_rows,"
                "row_confidence,cascade,external_triggers,reversibility,"
                "reversibility_reason,permanent_components,intent_summary"
            ),
            llm_involved=True,  # LLM used for intent_summary step
        )

        # ── Step 10: Log agent output to audit ──────────────────────────────
        elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
        contract_hash = hash_payload(contract.model_dump(mode="json"))
        await self.log_agent_output(
            output_summary=(
                f"Analysis: {op_type.value} on {contract.primary_table!r}, "
                f"{contract.estimated_primary_rows:,} rows, "
                f"reversibility={contract.reversibility}"
            ),
            contract_hash=contract_hash,
        )

        logger.info(
            "ANALYZER_AGENT complete",
            extra={
                "operation_id": contract.operation_id,
                "operation_type": op_type.value,
                "primary_table": contract.primary_table,
                "estimated_rows": contract.estimated_primary_rows,
                "cascade_tables": len(contract.cascade),
                "external_triggers": len(contract.external_triggers),
                "reversibility": (
                    contract.reversibility.value
                    if contract.reversibility
                    else None
                ),
                "elapsed_ms": round(elapsed_ms, 1),
            },
        )
        return contract

    # ── Private MCP helpers ──────────────────────────────────────────────────

    async def _count_affected_rows(self, table: str, condition: str) -> dict:
        if not table:
            return {"estimated_rows": 0, "confidence": 0.0}
        from orchestrator.mcp_client import MCPCallError
        try:
            return await self.call_tool(
                "postgres-reader",
                "estimate_row_count",
                {
                    "table": table,
                    "condition": condition or None,
                },
            )
        except MCPCallError as exc:
            logger.warning("count_affected_rows failed: %s", exc)
            return {"estimated_rows": 0, "confidence": 0.0}

    async def _detect_triggers(self, table: str) -> dict:
        from orchestrator.mcp_client import MCPCallError
        try:
            return await self.call_tool(
                "postgres-reader",
                "list_triggers",
                {"table": table},
            )
        except MCPCallError as exc:
            logger.warning("detect_triggers failed: %s", exc)
            return {
                "triggers": [],
                "has_permanent_side_effects": False,
                "non_transactional_triggers": [],
            }

    async def _trace_fk_graph(self, table: str) -> dict:
        from orchestrator.mcp_client import MCPCallError
        try:
            return await self.call_tool(
                "postgres-reader",
                "get_fk_graph",
                {"table": table},
            )
        except MCPCallError as exc:
            logger.warning("trace_fk_graph failed: %s", exc)
            return {"levels": [], "total_estimated_rows": 0}

    async def _check_soft_delete(self, table: str) -> dict:
        from orchestrator.mcp_client import MCPCallError
        try:
            return await self.call_tool(
                "postgres-reader",
                "check_soft_delete",
                {"table": table},
            )
        except MCPCallError as exc:
            logger.warning("check_soft_delete failed: %s", exc)
            return {"has_soft_delete": False}

    # ── LLM intent summarization ─────────────────────────────────────────────

    async def _get_intent_summary(self, contract: HandoffContract) -> str:
        """
        Call the Anthropic API for a structured-output intent summary.

        The LLM receives pre-computed structured fields only.
        Raw SQL is wrapped in <untrusted_input> tags — treated as data, not
        instructions (CLAUDE.md §22, invariant 11).

        Falls back to a deterministic rule-based summary on any API failure
        so that the pipeline never blocks on LLM availability.
        """
        try:
            import anthropic

            client = anthropic.AsyncAnthropic()
            prompt = build_intent_prompt(
                operation_type=contract.operation_type.value,
                primary_table=contract.primary_table,
                condition=contract.condition,
                estimated_rows=contract.estimated_primary_rows,
                cascade_tables=[
                    {"table": c.table, "estimated_rows": c.estimated_rows}
                    for c in contract.cascade[:5]
                ],
                raw_sql=contract.raw_sql,
            )

            response = await client.messages.create(
                model=_ANTHROPIC_MODEL,
                max_tokens=256,
                tools=[INTENT_SUMMARY_TOOL],
                tool_choice={"type": "tool", "name": INTENT_SUMMARY_TOOL_NAME},
                messages=[{"role": "user", "content": prompt}],
            )

            for block in response.content:
                if (
                    block.type == "tool_use"
                    and block.name == INTENT_SUMMARY_TOOL_NAME
                ):
                    summary = block.input.get("intent_summary", "")
                    if summary:
                        logger.debug(
                            "LLM intent summary produced",
                            extra={"operation_id": contract.operation_id},
                        )
                        return summary

        except Exception as exc:
            logger.warning(
                "LLM intent summary failed — using rule-based fallback: %s",
                exc,
                extra={"operation_id": contract.operation_id},
            )

        return _fallback_intent_summary(contract)


def _fallback_intent_summary(contract: HandoffContract) -> str:
    """
    Deterministic rule-based intent summary.
    Used when the Anthropic API is unavailable or returns an unexpected response.
    """
    op = contract.operation_type.value
    table = contract.primary_table or "unknown table"
    rows = contract.estimated_primary_rows
    cond = contract.condition

    parts = [f"{op} ~{rows:,} row(s) from {table!r}"]
    if cond:
        parts.append(f"WHERE {cond}")
    if contract.cascade:
        total_cascade = sum(c.estimated_rows for c in contract.cascade)
        parts.append(f"(cascade: ~{total_cascade:,} additional rows)")
    if contract.external_triggers:
        ext_count = sum(
            t.estimated_calls or 0 for t in contract.external_triggers
        )
        parts.append(f"(~{ext_count:,} external trigger calls)")
    return " ".join(parts)
