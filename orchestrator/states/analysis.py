"""
ANALYSIS state — delegates to the real Analyzer Agent (Phase 3).

The AnalyzerAgent (agents/analyzer/agent.py) owns:
  - SQL operation classification (deterministic)
  - Blast radius analysis via postgres-reader MCP
  - Reversibility classification (sql_analysis_lib — deterministic, no LLM)
  - Intent summarization (Anthropic structured output — the only LLM call)

The private helper functions below (_parse_sql_intent, _classify_reversibility,
etc.) are kept so that existing unit tests in tests/integration/test_analysis_state.py
continue to import and verify them. They are no longer called by run_analysis()
but remain tested as specification documentation.

Writes to contract: intent_summary, operation_type, primary_table, condition,
estimated_primary_rows, row_confidence, cascade, external_triggers,
reversibility, reversibility_reason, permanent_components.
"""

from __future__ import annotations

import logging
import re

from orchestrator.handoff_schema import (
    CascadeEntry,
    ExternalTrigger,
    HandoffContract,
    OperationType,
    ReversibilityClass,
)

logger = logging.getLogger(__name__)


async def run_analysis(contract: HandoffContract) -> HandoffContract:
    """
    Run ANALYSIS state by delegating to the real Analyzer Agent.

    The AnalyzerAgent handles selective re-analysis internally — if all
    analysis fields are fresh, it returns the contract unchanged.
    """
    from agents.analyzer.agent import AnalyzerAgent

    analyzer = AnalyzerAgent(contract.operation_id, contract.tenant_id)
    return await analyzer.analyze(contract)


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
