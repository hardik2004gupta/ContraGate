"""
policy-store MCP server — read-only policy rules from agent pipeline.

Permissions: SELECT only on contragate_app.policy_rules, policy_thresholds,
non_transactional_extensions, pii_registry tables.

Write access is via a separate admin tool — NOT exposed here.
An LLM must never override a hard policy rule (CLAUDE.md §22, invariant 2).

Tools exposed:
  get_active_policies             — All active policy rules for tenant
  is_pii_table                    — PII status for a table
  get_threshold                   — Configurable threshold value
  get_non_transactional_extensions — Extensions classified as non-transactional
  evaluate_rules                  — Evaluate operation against all active rules
"""

from __future__ import annotations

import os
from datetime import datetime

import psycopg2
import psycopg2.extras

from mcp_servers.base.server_base import ContraGateMCPServer
from mcp_servers.policy_store.rule_engine import evaluate_operation


DATABASE_URL = os.environ.get("DATABASE_URL", "")
PORT = int(os.environ.get("POLICY_STORE_PORT", "8014"))

server = ContraGateMCPServer("policy-store", port=PORT)


def _get_conn() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_session(readonly=True, autocommit=True)
    return conn


@server.tool("get_active_policies")
def get_active_policies(tenant_id: str) -> dict:
    """Return all active policy rules for the given tenant."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT rule_id, rule_name, description, condition, action, severity
                FROM contragate_app.policy_rules
                WHERE active = TRUE AND tenant_id = %s
                ORDER BY severity DESC, rule_id
                """,
                (tenant_id,),
            )
            rules = [dict(r) for r in cur.fetchall()]
        return {"policies": rules, "count": len(rules)}
    finally:
        conn.close()


@server.tool("is_pii_table")
def is_pii_table(table: str, tenant_id: str) -> dict:
    """Check whether the table is registered as PII for the given tenant."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT 1 FROM contragate_app.pii_registry WHERE table_name = %s
                """,
                (table,),
            )
            is_pii = cur.fetchone() is not None
        return {"table": table, "is_pii": is_pii}
    finally:
        conn.close()


@server.tool("get_threshold")
def get_threshold(key: str, tenant_id: str) -> dict:
    """Return a configurable threshold value by key."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT value FROM contragate_app.policy_thresholds
                WHERE key = %s AND tenant_id = %s
                """,
                (key, tenant_id),
            )
            row = cur.fetchone()
        if row:
            return {"key": key, "value": float(row[0])}

        # Fall back to hardcoded defaults
        defaults = {
            "explain_cost_threshold": 50000.0,
            "explain_cost_pii_threshold": 100000.0,
            "bulk_row_threshold": 10000.0,
            "jaccard_similarity_threshold": 0.3,
            "auto_reject_lookback_days": 30.0,
            "review_timeout_seconds": 1800.0,
            "sandbox_statement_timeout_ms": 5000.0,
        }
        return {"key": key, "value": defaults.get(key, 0.0)}
    finally:
        conn.close()


@server.tool("get_non_transactional_extensions")
def get_non_transactional_extensions(tenant_id: str) -> dict:
    """
    Return the list of extensions classified as non-transactional.
    Used by trigger_detector to identify PERMANENT external side effects.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT extname FROM contragate_app.non_transactional_extensions
                WHERE tenant_id = %s
                """,
                (tenant_id,),
            )
            rows = [r[0] for r in cur.fetchall()]
        if not rows:
            rows = ["pg_net", "dblink"]
        return {"extensions": rows}
    finally:
        conn.close()


@server.tool("evaluate_rules")
def evaluate_rules(
    operation_type: str,
    tables: list[str],
    estimated_rows: int,
    source_type: str,
    has_external_triggers: bool,
    submission_hour: int,
    tenant_id: str,
    explain_cost: float = 0.0,
) -> dict:
    """
    Evaluate an operation against all active policy rules.
    Returns triggered rules and the recommended action.
    Policy enforcement is deterministic — no LLM involved.
    """
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT rule_id, rule_name, description, condition, action, severity
                FROM contragate_app.policy_rules
                WHERE active = TRUE AND tenant_id = %s
                ORDER BY severity DESC
                """,
                (tenant_id,),
            )
            all_rules = [dict(r) for r in cur.fetchall()]

            # Resolve PII tables
            cur.execute(
                "SELECT table_name FROM contragate_app.pii_registry"
            )
            pii_tables = {r["table_name"] for r in cur.fetchall()}

        return evaluate_operation(
            operation_type=operation_type,
            tables=tables,
            estimated_rows=estimated_rows,
            source_type=source_type,
            has_external_triggers=has_external_triggers,
            submission_hour=submission_hour,
            explain_cost=explain_cost,
            pii_tables=pii_tables,
            all_rules=all_rules,
        )
    finally:
        conn.close()


if __name__ == "__main__":
    server.run()
