"""
Analyzer Agent tool implementations — pure functions.

These are the transformation and parsing functions used by AnalyzerAgent.
They take raw MCP response dicts or SQL strings as input and return
HandoffContract-compatible structures.

No MCP calls here — all data fetching lives in AnalyzerAgent.
No LLM calls here — all LLM interaction lives in AnalyzerAgent.

The separation enables unit testing without MCP servers or API keys.

Tools (CLAUDE.md §8):
  classify_operation_type    — DDL / DELETE / UPDATE / INSERT / SELECT
  parse_sql_intent           — extract (primary_table, where_condition)
  build_cascade_entries      — transform get_fk_graph MCP response
  build_external_triggers    — transform list_triggers MCP response
  build_trigger_analysis_from_mcp — reconstruct TriggerAnalysis dataclass
  estimate_api_fanout        — one API call per row per non-transactional trigger
  identify_permanent_components — list permanent side-effect components
"""
from __future__ import annotations

import re

from orchestrator.handoff_schema import (
    CascadeEntry,
    ExternalTrigger,
    OperationType,
    ReversibilityClass,
)
from sql_analysis_lib.trigger_detector import TriggerAnalysis, TriggerInfo


_DDL_PATTERN = re.compile(r"^\s*(DROP|TRUNCATE|ALTER|CREATE)\b", re.IGNORECASE)


def classify_operation_type(raw_sql: str) -> OperationType:
    """
    Deterministic SQL operation type from first keyword.
    No LLM. No database access. Pure string analysis.
    """
    sql = raw_sql.strip()
    if _DDL_PATTERN.match(sql):
        return OperationType.DDL
    parts = sql.split()
    if not parts:
        return OperationType.UNKNOWN
    verb = parts[0].upper()
    return {
        "DELETE": OperationType.DELETE,
        "UPDATE": OperationType.UPDATE,
        "INSERT": OperationType.INSERT,
        "SELECT": OperationType.SELECT,
    }.get(verb, OperationType.UNKNOWN)


def parse_sql_intent(raw_sql: str) -> tuple[str, str]:
    """
    Extract (primary_table, where_condition) from SQL.
    Returns ("", "") when unparseable.

    Routes by first keyword to avoid ambiguity: DELETE...FROM and
    SELECT...FROM share the same FROM regex, but the first keyword resolves
    which branch to take.
    """
    sql = raw_sql.strip()
    parts = sql.split()
    if not parts:
        return "", ""

    first = parts[0].upper()

    if first in ("DELETE", "SELECT"):
        m = re.search(r"\bFROM\s+(\w+)", sql, re.IGNORECASE)
        if m:
            return m.group(1), _extract_where(sql)

    elif first == "UPDATE":
        m = re.search(r"\bUPDATE\s+(\w+)", sql, re.IGNORECASE)
        if m:
            return m.group(1), _extract_where(sql)

    elif first == "INSERT":
        m = re.search(r"\bINTO\s+(\w+)", sql, re.IGNORECASE)
        if m:
            return m.group(1), ""

    elif first in ("DROP", "TRUNCATE", "ALTER", "CREATE"):
        m = re.search(r"\bTABLE\s+(?:IF\s+EXISTS\s+)?(\w+)", sql, re.IGNORECASE)
        if m:
            return m.group(1), ""

    return "", ""


def _extract_where(sql: str) -> str:
    """Extract the WHERE clause body, stopping at ORDER BY / LIMIT / etc."""
    m = re.search(
        r"\bWHERE\s+(.+?)(?=\s+(?:ORDER\s+BY|LIMIT|GROUP\s+BY|HAVING|RETURNING)|;|$)",
        sql,
        re.IGNORECASE | re.DOTALL,
    )
    return m.group(1).strip() if m else ""


def build_cascade_entries(
    fk_graph_result: dict,
    root_rows: int,  # noqa: ARG001  (kept for API symmetry; server pre-scales)
) -> list[CascadeEntry]:
    """
    Transform postgres_reader.get_fk_graph response into CascadeEntry list.

    The MCP server already scales estimated_rows by the FK ratio — we use
    the server-provided values directly.
    """
    entries: list[CascadeEntry] = []
    for lvl in fk_graph_result.get("levels", []):
        entries.append(CascadeEntry(
            table=lvl.get("table_name", "unknown"),
            estimated_rows=lvl.get("estimated_rows", 0),
            actual_rows=None,
            cascade_action=lvl.get("cascade_action", "CASCADE"),
            depth=lvl.get("depth", 1),
        ))
    return entries


def build_external_triggers(
    list_triggers_result: dict,
) -> list[ExternalTrigger]:
    """
    Transform postgres_reader.list_triggers response into ExternalTrigger list.

    Only non-transactional triggers produce ExternalTrigger entries.
    Event info is cross-referenced from the full trigger list.
    """
    event_by_name: dict[str, str] = {
        t.get("trigger_name", ""): t.get("event", "UNKNOWN")
        for t in list_triggers_result.get("triggers", [])
    }

    external: list[ExternalTrigger] = []
    for t in list_triggers_result.get("non_transactional_triggers", []):
        name = t.get("trigger_name", "unknown")
        external.append(ExternalTrigger(
            trigger_name=name,
            event=event_by_name.get(name, "UNKNOWN"),
            extension=t.get("non_transactional_extension") or "unknown",
            estimated_calls=None,
            target_endpoint=t.get("target_endpoint"),
            sandbox_log_entry=None,
        ))
    return external


def build_trigger_analysis_from_mcp(
    table: str,
    list_triggers_result: dict,
) -> TriggerAnalysis:
    """
    Reconstruct a sql_analysis_lib TriggerAnalysis dataclass from the
    postgres_reader.list_triggers MCP response.

    Required by sql_analysis_lib.classify_reversibility() which expects
    the typed TriggerAnalysis object.
    """
    nt_names: set[str] = {
        t.get("trigger_name", "")
        for t in list_triggers_result.get("non_transactional_triggers", [])
    }

    infos: list[TriggerInfo] = []
    for t in list_triggers_result.get("triggers", []):
        name = t.get("trigger_name", "")
        invokes_nt = t.get("invokes_non_transactional", False) or name in nt_names
        infos.append(TriggerInfo(
            trigger_name=name,
            table_name=t.get("table_name", table),
            event=t.get("event", "UNKNOWN"),
            timing=t.get("timing", "AFTER"),
            function_name=t.get("function_name", ""),
            invokes_non_transactional=invokes_nt,
            non_transactional_extension=t.get("non_transactional_extension"),
            estimated_external_calls=None,
            target_endpoint=t.get("target_endpoint"),
        ))

    non_transactional = [t for t in infos if t.invokes_non_transactional]

    return TriggerAnalysis(
        table=table,
        triggers=infos,
        has_permanent_side_effects=(
            list_triggers_result.get("has_permanent_side_effects", False)
            or len(non_transactional) > 0
        ),
        non_transactional_triggers=non_transactional,
    )


def estimate_api_fanout(
    external_triggers: list[ExternalTrigger],
    row_count: int,
) -> list[ExternalTrigger]:
    """
    Set estimated_calls for each external trigger.
    Default model: one external call per affected row per non-transactional trigger.
    Returns a new list — does not mutate the input.
    """
    return [
        ExternalTrigger(
            trigger_name=t.trigger_name,
            event=t.event,
            extension=t.extension,
            estimated_calls=row_count,
            target_endpoint=t.target_endpoint,
            sandbox_log_entry=t.sandbox_log_entry,
        )
        for t in external_triggers
    ]


def identify_permanent_components(
    reversibility: ReversibilityClass | None,
    operation_type: OperationType,
    primary_table: str,
    external_triggers: list[ExternalTrigger],
    cascade: list[CascadeEntry],
) -> list[str]:
    """
    Enumerate the components of an operation that cannot be automatically recovered.
    Returns empty list for non-PERMANENT reversibility.
    """
    if reversibility != ReversibilityClass.PERMANENT:
        return []

    components: list[str] = []
    if operation_type == OperationType.DDL:
        components.append("DDL schema change")
    for t in external_triggers:
        components.append(f"External {t.extension} trigger: {t.trigger_name}")
    if primary_table:
        components.append(f"Row deletion from {primary_table}")
    for c in cascade:
        components.append(f"Cascade deletion from {c.table} (~{c.estimated_rows:,} rows)")
    return components
