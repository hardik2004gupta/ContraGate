"""
RISK_GATE state — Read Impact Gate (EXPLAIN) + Policy Gate.

Procedure (CLAUDE.md §14, §15):
  1. Run EXPLAIN (FORMAT JSON) via postgres_reader MCP server (always first, even for writes)
  2. Extract estimated_rows, cost, scan_type from EXPLAIN output
  3. Call policy_store.evaluate_rules() with operation metadata
  4. Determine routing: auto_reject | auto_execute | full_pipeline
  5. Write policy_violations, auto_reject flags, risk_score to HandoffContract

The Read Impact Gate runs BEFORE any agent pipeline. It does NOT run after.
Policy enforcement is deterministic — no LLM involvement.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from orchestrator import mcp_client
from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    PolicyViolation,
    RiskTier,
)

logger = logging.getLogger(__name__)

# Default thresholds — overridden by policy_store values at runtime
_EXPLAIN_COST_THRESHOLD = 50_000.0
_EXPLAIN_PII_COST_THRESHOLD = 100_000.0


def _explain_sql(raw_sql: str) -> str:
    """Wrap raw SQL in EXPLAIN (FORMAT JSON) — safe read-only analysis."""
    stripped = raw_sql.strip().rstrip(";")
    return f"EXPLAIN (FORMAT JSON) {stripped}"


def _parse_explain_result(explain_data: Any) -> dict:
    """
    Extract cost and row estimates from EXPLAIN JSON output.
    Returns defaults on parse failure — never crashes the pipeline.
    """
    result = {
        "total_cost": 0.0,
        "estimated_rows": 0,
        "scan_type": "unknown",
        "partitions_involved": False,
    }
    try:
        if isinstance(explain_data, list) and explain_data:
            plan = explain_data[0].get("Plan", {})
        elif isinstance(explain_data, dict):
            plan = explain_data.get("Plan", explain_data)
        else:
            return result

        result["total_cost"] = float(plan.get("Total Cost", plan.get("total_cost", 0.0)))
        result["estimated_rows"] = int(plan.get("Plan Rows", plan.get("plan_rows", 0)))
        node_type = plan.get("Node Type", plan.get("node_type", ""))
        result["scan_type"] = "seq_scan" if "Seq" in node_type else "index_scan"
        result["partitions_involved"] = "Append" in str(plan)
    except (KeyError, ValueError, TypeError, IndexError):
        pass
    return result


async def run_risk_gate(contract: HandoffContract) -> HandoffContract:
    """
    Execute Read Impact Gate then Policy Gate.
    Returns updated HandoffContract with risk routing decision.
    """
    start = datetime.now(timezone.utc)

    # ── Step 1: Read Impact Gate ─────────────────────────────────────────────
    explain_cost = 0.0
    explain_rows = 0
    explain_available = True

    try:
        explain_sql = _explain_sql(contract.raw_sql)
        result = await mcp_client.postgres_reader(
            "run_explain_tool",
            {"sql": explain_sql, "tenant_id": contract.tenant_id},
        )
        parsed = _parse_explain_result(result.get("explain_result", result))
        explain_cost = parsed["total_cost"]
        explain_rows = parsed["estimated_rows"]
    except mcp_client.MCPCallError as exc:
        logger.warning("EXPLAIN unavailable: %s", exc)
        explain_available = False
        # Default to conservative cost to avoid incorrectly routing to fast path
        explain_cost = 999_999.0

    # ── Step 2: Policy Gate ──────────────────────────────────────────────────
    submission_hour = start.hour

    try:
        tables = _extract_tables(contract)
        policy_result = await mcp_client.policy_store(
            "evaluate_rules",
            {
                "operation_type": contract.operation_type.value,
                "tables": tables,
                "estimated_rows": explain_rows,
                "source_type": contract.source_type.value,
                "has_external_triggers": False,  # Not yet analyzed; conservative default
                "submission_hour": submission_hour,
                "tenant_id": contract.tenant_id,
                "explain_cost": explain_cost,
            },
        )
    except mcp_client.MCPCallError as exc:
        logger.warning("Policy store unavailable: %s — applying safe defaults", exc)
        policy_result = {
            "triggered_rules": [],
            "has_hard_violation": False,
            "has_soft_violation": False,
            "required_tier": None,
            "auto_reject_reason": None,
        }

    # ── Step 3: Apply policy results to contract ─────────────────────────────
    violations = []
    for rule in policy_result.get("triggered_rules", []):
        violations.append(PolicyViolation(
            rule_id=rule["rule_id"],
            rule_name=rule["rule_name"],
            severity=rule["severity"],
            description=rule["description"],
        ))

    contract.policy_violations = violations
    contract.auto_reject_triggered = policy_result.get("has_hard_violation", False)
    contract.auto_reject_reason = policy_result.get("auto_reject_reason")
    contract.required_tier_from_policy = policy_result.get("required_tier")

    # ── Step 4: Compute preliminary risk score ───────────────────────────────
    contract.risk_score = _compute_risk_score(
        contract, explain_cost, explain_rows, bool(violations)
    )

    # Force FULL_CONTRACT if required by policy
    if contract.required_tier_from_policy == "FULL_CONTRACT":
        contract.risk_tier = RiskTier.FULL_CONTRACT

    if contract.auto_reject_triggered:
        contract.approval_state = ApprovalState.AUTO_REJECTED

    elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000
    contract.add_provenance(
        agent="RISK_GATE",
        field_written=(
            "policy_violations,auto_reject_triggered,auto_reject_reason,"
            "required_tier_from_policy,risk_score"
        ),
        llm_involved=False,
    )

    logger.info(
        "RISK_GATE complete",
        extra={
            "operation_id": contract.operation_id,
            "explain_cost": explain_cost,
            "auto_reject": contract.auto_reject_triggered,
            "violations": len(violations),
            "risk_score": contract.risk_score,
            "elapsed_ms": round(elapsed_ms, 1),
        },
    )
    return contract


def _extract_tables(contract: HandoffContract) -> list[str]:
    """
    Heuristic table extraction from raw SQL for policy gate.
    The Analyzer Agent does the proper parse in Phase 3 — this is a fast pre-filter.
    """
    import re
    raw = contract.raw_sql.upper()
    tables: list[str] = []
    # FROM table, JOIN table, INTO table, UPDATE table, TABLE table
    patterns = [
        r"FROM\s+(\w+)",
        r"JOIN\s+(\w+)",
        r"INTO\s+(\w+)",
        r"UPDATE\s+(\w+)",
        r"TABLE\s+(\w+)",
        r"TRUNCATE\s+(\w+)",
    ]
    for pat in patterns:
        for match in re.finditer(pat, raw):
            name = match.group(1).lower()
            if name not in ("where", "set", "on", "select", "values"):
                tables.append(name)
    return list(dict.fromkeys(tables))  # deduplicate, preserve order


def _compute_risk_score(
    contract: HandoffContract,
    explain_cost: float,
    explain_rows: int,
    has_violations: bool,
) -> float:
    """
    Preliminary risk score (0.0–1.0) from pre-analysis signals.
    Full score is computed by the Contract Agent after analysis.
    """
    score = 0.0
    op = contract.operation_type

    # Base score by operation type
    if op == OperationType.SELECT:
        score = 0.05
    elif op == OperationType.INSERT:
        score = 0.2
    elif op == OperationType.UPDATE:
        score = 0.4
    elif op == OperationType.DELETE:
        score = 0.6
    elif op == OperationType.DDL:
        score = 0.85
    else:
        score = 0.5

    # EXPLAIN cost modifier
    if explain_cost > 500_000:
        score = min(1.0, score + 0.2)
    elif explain_cost > 100_000:
        score = min(1.0, score + 0.1)
    elif explain_cost > _EXPLAIN_COST_THRESHOLD:
        score = min(1.0, score + 0.05)

    # Policy violation modifier
    if has_violations:
        score = min(1.0, score + 0.1)

    # External input modifier
    from orchestrator.handoff_schema import SourceType
    if contract.source_type == SourceType.EXTERNAL_USER_INPUT:
        score = min(1.0, score + 0.15)

    return round(score, 3)
