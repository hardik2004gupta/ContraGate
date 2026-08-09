"""
Proxy-level Risk Gate — thin coordinator for the Read Impact Gate and Policy Gate.

This module is the proxy-side entry point that decides whether a tool call
should be fast-pathed or sent through the full LangGraph pipeline.

Note: The actual EXPLAIN and policy evaluation logic lives in
orchestrator/states/risk_gate.py. This module provides a proxy-layer
quick-check so the proxy can decide whether to:
  a) Execute immediately (fast path, auto-execute)
  b) Return PENDING and kick off the full orchestrator pipeline

The proxy calls this before starting the LangGraph workflow.
For non-fast-path operations, the orchestrator's RISK_GATE state re-runs
the same analysis as part of the full typed HandoffContract pipeline.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

POSTGRES_READER_URL = os.environ.get(
    "POSTGRES_READER_URL", "http://contragate-mcp:8010"
)
POLICY_STORE_URL = os.environ.get(
    "POLICY_STORE_URL", "http://contragate-mcp:8014"
)
MCP_API_KEY = os.environ.get("CONTRAGATE_MCP_API_KEY", "dev-mcp-key")
EXPLAIN_COST_THRESHOLD = float(os.environ.get("CONTRAGATE_EXPLAIN_THRESHOLD", "50000"))


class RiskGateResult:
    __slots__ = (
        "routing", "explain_cost", "explain_rows", "hard_violation",
        "auto_reject_reason", "policy_required_tier",
    )

    def __init__(
        self,
        routing: str,
        explain_cost: float = 0.0,
        explain_rows: int = 0,
        hard_violation: bool = False,
        auto_reject_reason: str | None = None,
        policy_required_tier: str | None = None,
    ) -> None:
        self.routing = routing  # "auto_execute" | "auto_reject" | "full_pipeline"
        self.explain_cost = explain_cost
        self.explain_rows = explain_rows
        self.hard_violation = hard_violation
        self.auto_reject_reason = auto_reject_reason
        self.policy_required_tier = policy_required_tier


async def quick_check(
    sql: str,
    operation_type: str,
    source_type: str,
    tenant_id: str,
) -> RiskGateResult:
    """
    Quick proxy-level risk gate check.

    Runs EXPLAIN and a lightweight policy check to determine fast-path eligibility.
    If unavailable (MCP servers not yet healthy), defaults to full_pipeline (safe fallback).
    """
    import re

    explain_cost = 999_999.0  # Conservative default if EXPLAIN unavailable
    explain_rows = 0
    hard_violation = False
    auto_reject_reason = None

    # ── Read Impact Gate (EXPLAIN) ───────────────────────────────────────────
    is_write = bool(re.match(
        r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b",
        sql, re.IGNORECASE,
    ))

    try:
        # Safety: strip trailing semicolons, then reject any SQL that still contains
        # embedded semicolons — this blocks multi-statement injection attempts.
        # PostgreSQL would reject them anyway (EXPLAIN takes one statement), but
        # defense-in-depth means we never send the request at all.
        _clean_sql = sql.strip().rstrip(';').strip()
        if ';' in _clean_sql:
            logger.warning(
                "EXPLAIN safety: SQL contains embedded semicolons — skipping EXPLAIN, "
                "using conservative cost estimate"
            )
            raise ValueError("multi-statement SQL rejected at proxy EXPLAIN safety check")
        explain_sql = f"EXPLAIN (FORMAT JSON) {_clean_sql}"
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{POSTGRES_READER_URL}/call",
                json={"tool": "run_explain_tool", "arguments": {"sql": explain_sql, "tenant_id": tenant_id}},
                headers={"X-ContraGate-Key": MCP_API_KEY},
            )
        if resp.status_code == 200:
            data = resp.json().get("result", {})
            explain_result = data.get("explain_result", data)
            if isinstance(explain_result, list) and explain_result:
                plan = explain_result[0].get("Plan", {})
            elif isinstance(explain_result, dict):
                plan = explain_result.get("Plan", explain_result)
            else:
                plan = {}
            explain_cost = float(plan.get("Total Cost", plan.get("total_cost", 999_999.0)))
            explain_rows = int(plan.get("Plan Rows", plan.get("plan_rows", 0)))
    except Exception as exc:
        logger.debug("EXPLAIN unavailable in quick_check: %s", exc)

    # ── Quick policy check for hard violations ───────────────────────────────
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.post(
                f"{POLICY_STORE_URL}/call",
                json={
                    "tool": "evaluate_rules",
                    "arguments": {
                        "operation_type": operation_type,
                        "tables": [],  # Heuristic only — Analyzer resolves tables fully
                        "estimated_rows": explain_rows,
                        "source_type": source_type,
                        "has_external_triggers": False,
                        "submission_hour": datetime.now(timezone.utc).hour,
                        "tenant_id": tenant_id,
                        "explain_cost": explain_cost,
                    },
                },
                headers={"X-ContraGate-Key": MCP_API_KEY},
            )
        if resp.status_code == 200:
            policy = resp.json().get("result", {})
            hard_violation = policy.get("has_hard_violation", False)
            auto_reject_reason = policy.get("auto_reject_reason")
    except Exception as exc:
        logger.debug("Policy store unavailable in quick_check: %s", exc)

    # ── Routing decision ─────────────────────────────────────────────────────
    if hard_violation:
        return RiskGateResult(
            routing="auto_reject",
            explain_cost=explain_cost,
            explain_rows=explain_rows,
            hard_violation=True,
            auto_reject_reason=auto_reject_reason,
        )

    # Fast path: SELECT, low cost, no PII, no violations
    if (
        operation_type == "SELECT"
        and not is_write
        and explain_cost < EXPLAIN_COST_THRESHOLD
        and source_type == "internal_system"
    ):
        return RiskGateResult(
            routing="auto_execute",
            explain_cost=explain_cost,
            explain_rows=explain_rows,
        )

    return RiskGateResult(
        routing="full_pipeline",
        explain_cost=explain_cost,
        explain_rows=explain_rows,
    )
