"""
ANALYSIS state — Phase 2 stub invoking Analyzer Agent.

Phase 2 behavior: calls the postgres_reader MCP server for real schema
data (FK graph, triggers, row estimates) and derives a structured blast_radius
and reversibility classification using sql_analysis_lib functions.

Phase 2 does NOT invoke a real LLM agent. The analytical logic runs directly
in this state using sql_analysis_lib — the actual Analyzer Agent wrapping
this in structured LLM prompts is Phase 3.

Writes to contract: intent_summary, operation_type, primary_table, condition,
estimated_primary_rows, row_confidence, cascade, external_triggers,
reversibility, reversibility_reason, permanent_components.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime

from orchestrator import mcp_client
from orchestrator.handoff_schema import (
    CascadeEntry,
    ExternalTrigger,
    HandoffContract,
    OperationType,
    ReversibilityClass,
)
from orchestrator.guards import needs_selective_reanalysis

logger = logging.getLogger(__name__)


async def run_analysis(contract: HandoffContract) -> HandoffContract:
    """
    Run blast radius analysis and reversibility classification.

    In Phase 2: uses postgres_reader MCP server + sql_analysis_lib logic.
    In Phase 3: will be replaced by Analyzer Agent LLM orchestration.
    """
    start = datetime.utcnow()

    # Skip fields that are still fresh on selective re-analysis
    if needs_selective_reanalysis(contract):
        analysis_fields = {
            "intent_summary", "primary_table", "estimated_primary_rows",
            "cascade", "external_triggers", "reversibility",
        }
        fresh = set(contract.stale_fields) if contract.stale_fields else set()
        # If none of the analysis fields are stale, skip entirely
        if not (analysis_fields & set(contract.stale_fields)):
            logger.info(
                "ANALYSIS skipped — all fields fresh",
                extra={"operation_id": contract.operation_id},
            )
            return contract

    # ── Parse intent from raw SQL ────────────────────────────────────────────
    primary_table, condition = _parse_sql_intent(contract.raw_sql)
    if primary_table:
        contract.primary_table = primary_table
    if condition:
        contract.condition = condition

    # Build human-readable intent summary
    op = contract.operation_type.value
    contract.intent_summary = (
        f"{op} on {contract.primary_table or 'unknown table'}"
        + (f" WHERE {condition}" if condition else "")
    )

    # ── Row estimation via postgres_reader ───────────────────────────────────
    if contract.primary_table:
        try:
            row_result = await mcp_client.postgres_reader(
                "estimate_row_count",
                {
                    "table": contract.primary_table,
                    "condition": condition or "",
                    "tenant_id": contract.tenant_id,
                },
            )
            contract.estimated_primary_rows = row_result.get("estimated_rows", 0)
            contract.row_confidence = row_result.get("confidence_score", 0.5)
        except mcp_client.MCPCallError as exc:
            logger.warning("Row estimation failed: %s", exc)
            contract.estimated_primary_rows = 0
            contract.row_confidence = 0.0

    # ── FK cascade analysis ──────────────────────────────────────────────────
    if contract.primary_table and contract.operation_type in (
        OperationType.DELETE, OperationType.UPDATE
    ):
        try:
            fk_result = await mcp_client.postgres_reader(
                "get_fk_graph",
                {
                    "table": contract.primary_table,
                    "tenant_id": contract.tenant_id,
                },
            )
            contract.cascade = _build_cascade_entries(
                fk_result.get("dependents", []),
                contract.estimated_primary_rows,
            )
        except mcp_client.MCPCallError as exc:
            logger.warning("FK graph unavailable: %s", exc)
            contract.cascade = []

    # ── Trigger detection ────────────────────────────────────────────────────
    if contract.primary_table:
        try:
            trigger_result = await mcp_client.postgres_reader(
                "list_triggers",
                {
                    "table": contract.primary_table,
                    "tenant_id": contract.tenant_id,
                },
            )
            contract.external_triggers = _build_external_triggers(
                trigger_result.get("triggers", [])
            )
        except mcp_client.MCPCallError as exc:
            logger.warning("Trigger detection failed: %s", exc)
            contract.external_triggers = []

    # ── Soft-delete column check ─────────────────────────────────────────────
    has_soft_delete = False
    if contract.primary_table and contract.operation_type == OperationType.DELETE:
        try:
            sd_result = await mcp_client.postgres_reader(
                "check_soft_delete",
                {
                    "table": contract.primary_table,
                    "tenant_id": contract.tenant_id,
                },
            )
            has_soft_delete = sd_result.get("has_soft_delete", False)
        except mcp_client.MCPCallError:
            has_soft_delete = False

    # ── Reversibility classification (deterministic — no LLM) ───────────────
    contract.reversibility, contract.reversibility_reason = _classify_reversibility(
        contract, has_soft_delete
    )

    # Mark permanent components
    contract.permanent_components = _identify_permanent_components(contract)

    elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
    contract.add_provenance(
        agent="ANALYSIS_STUB",
        field_written=(
            "intent_summary,primary_table,condition,estimated_primary_rows,"
            "row_confidence,cascade,external_triggers,reversibility,"
            "reversibility_reason,permanent_components"
        ),
        llm_involved=False,
    )

    logger.info(
        "ANALYSIS complete",
        extra={
            "operation_id": contract.operation_id,
            "primary_table": contract.primary_table,
            "estimated_rows": contract.estimated_primary_rows,
            "cascade_tables": len(contract.cascade),
            "reversibility": contract.reversibility.value if contract.reversibility else None,
            "elapsed_ms": round(elapsed_ms, 1),
        },
    )
    return contract


def _parse_sql_intent(raw_sql: str) -> tuple[str, str]:
    """Extract primary table and WHERE condition from raw SQL."""
    sql = raw_sql.strip()

    # UPDATE table SET ... WHERE condition
    m = re.search(r"UPDATE\s+(\w+)", sql, re.IGNORECASE)
    if m:
        table = m.group(1)
        cond = _extract_where(sql)
        return table, cond

    # DELETE FROM table WHERE condition
    m = re.search(r"FROM\s+(\w+)", sql, re.IGNORECASE)
    if m:
        table = m.group(1)
        cond = _extract_where(sql)
        return table, cond

    # INSERT INTO table
    m = re.search(r"INTO\s+(\w+)", sql, re.IGNORECASE)
    if m:
        return m.group(1), ""

    # SELECT ... FROM table
    m = re.search(r"FROM\s+(\w+)", sql, re.IGNORECASE)
    if m:
        table = m.group(1)
        cond = _extract_where(sql)
        return table, cond

    # TRUNCATE / DROP TABLE table
    m = re.search(r"TABLE\s+(\w+)", sql, re.IGNORECASE)
    if m:
        return m.group(1), ""

    return "", ""


def _extract_where(sql: str) -> str:
    m = re.search(r"WHERE\s+(.+?)(?:\s+ORDER|\s+LIMIT|\s+GROUP|\s+HAVING|;|$)", sql, re.IGNORECASE | re.DOTALL)
    return m.group(1).strip() if m else ""


def _build_cascade_entries(dependents: list[dict], parent_rows: int) -> list[CascadeEntry]:
    entries = []
    for dep in dependents:
        selectivity = dep.get("selectivity", 2.0)  # conservative default
        estimated = int(parent_rows * selectivity)
        entries.append(CascadeEntry(
            table=dep.get("table", "unknown"),
            estimated_rows=estimated,
            actual_rows=None,
            cascade_action=dep.get("action", "CASCADE"),
            depth=dep.get("depth", 1),
        ))
    return entries


def _build_external_triggers(triggers: list[dict]) -> list[ExternalTrigger]:
    external = []
    for t in triggers:
        if t.get("is_non_transactional", False):
            external.append(ExternalTrigger(
                trigger_name=t.get("trigger_name", "unknown"),
                event=t.get("event", "UNKNOWN"),
                extension=t.get("extension", "unknown"),
                estimated_calls=None,
                target_endpoint=t.get("target_endpoint"),
                sandbox_log_entry=None,
            ))
    return external


def _classify_reversibility(
    contract: HandoffContract,
    has_soft_delete: bool,
) -> tuple[ReversibilityClass, str]:
    """
    Deterministic reversibility classification.
    Priority order from CLAUDE.md §13 — no LLM calls.
    """
    op = contract.operation_type

    # Priority 1: DDL is always PERMANENT
    if op == OperationType.DDL:
        return (
            ReversibilityClass.PERMANENT,
            "DDL operations (DROP, ALTER, TRUNCATE) are irreversible without restore",
        )

    # Priority 2: Non-transactional external effects → PERMANENT for external components
    if contract.external_triggers:
        extensions = {t.extension for t in contract.external_triggers}
        return (
            ReversibilityClass.PERMANENT,
            f"Non-transactional external effects via {', '.join(extensions)} cannot be rolled back",
        )

    # Priority 3: DELETE/UPDATE on table without soft-delete and no PITR confirmation
    if op in (OperationType.DELETE, OperationType.UPDATE) and not has_soft_delete:
        # In Phase 2: assume no PITR without explicit confirmation
        return (
            ReversibilityClass.PERMANENT,
            f"Table {contract.primary_table!r} has no soft-delete column — deletion is permanent without PITR",
        )

    # Priority 4: DELETE/UPDATE with soft-delete → automated recovery possible
    if op in (OperationType.DELETE, OperationType.UPDATE) and has_soft_delete:
        return (
            ReversibilityClass.REVERSIBLE_AUTOMATED,
            "Soft-delete column present — recovery by setting deleted_at = NULL",
        )

    # Priority 5: INSERT → reversible via DELETE
    if op == OperationType.INSERT:
        return (
            ReversibilityClass.REVERSIBLE_AUTOMATED,
            "INSERT can be reversed by deleting the inserted rows using the same condition",
        )

    # Priority 6: SELECT → no data modification
    if op == OperationType.SELECT:
        return (
            ReversibilityClass.REVERSIBLE_AUTOMATED,
            "SELECT operations do not modify data",
        )

    return (
        ReversibilityClass.PARTIAL,
        "Reversibility depends on downstream state — review required",
    )


def _identify_permanent_components(contract: HandoffContract) -> list[str]:
    components = []
    if contract.reversibility == ReversibilityClass.PERMANENT:
        if contract.operation_type == OperationType.DDL:
            components.append("DDL schema change")
        if contract.external_triggers:
            for t in contract.external_triggers:
                components.append(f"External {t.extension} trigger: {t.trigger_name}")
        if contract.primary_table:
            components.append(f"Row deletion from {contract.primary_table}")
        for c in contract.cascade:
            components.append(f"Cascade deletion from {c.table} (~{c.estimated_rows} rows)")
    return components
