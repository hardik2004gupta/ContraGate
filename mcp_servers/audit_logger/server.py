"""
audit-logger MCP server — append-only tamper-evident audit log.

Permissions: INSERT only on contragate_app.audit_log.
The database role (cg_audit_writer) has no SELECT, UPDATE, or DELETE.
Administrative deletion requires a separate authorization path.

Every write computes a SHA-256 checksum of the row content before insert.
verify_checksum allows detection of out-of-band tampering.

Tools exposed:
  log_tool_call     — Record an agent tool invocation
  log_agent_output  — Record an agent's final output for a state
  log_human_decision — Record the human's approval decision
  log_execution     — Record the execution result
  log_auto_reject   — Record a policy-gate auto-rejection
  list_operations   — List audit records (read role required)
  verify_checksum   — Verify integrity of a specific record

Security invariant (CLAUDE.md §22, invariant 7):
No mutable audit history from normal application code paths.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime

import psycopg2
import psycopg2.extras

from mcp_servers.base.server_base import ContraGateMCPServer


DATABASE_URL = os.environ.get("DATABASE_URL", "")
PORT = int(os.environ.get("AUDIT_LOGGER_PORT", "8013"))

server = ContraGateMCPServer("audit-logger", port=PORT)


def _get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(DATABASE_URL)


def _checksum(data: dict) -> str:
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def _ensure_audit_table(conn: psycopg2.extensions.connection) -> None:
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS contragate_app.audit_log (
                id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                operation_id    TEXT NOT NULL,
                tenant_id       TEXT NOT NULL DEFAULT 'demo_tenant',
                event_type      TEXT NOT NULL,
                agent_name      TEXT,
                event_data      JSONB NOT NULL,
                checksum        TEXT NOT NULL,
                logged_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_operation_id "
            "ON contragate_app.audit_log(operation_id)"
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_audit_logged_at "
            "ON contragate_app.audit_log(logged_at)"
        )
    conn.commit()


def _append(
    conn: psycopg2.extensions.connection,
    operation_id: str,
    event_type: str,
    event_data: dict,
    agent_name: str | None = None,
) -> dict:
    _ensure_audit_table(conn)
    record_id = str(uuid.uuid4())
    checksum = _checksum({
        "id": record_id,
        "operation_id": operation_id,
        "event_type": event_type,
        "agent_name": agent_name,
        "event_data": event_data,
    })
    with conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO contragate_app.audit_log
                (id, operation_id, event_type, agent_name, event_data, checksum)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            (record_id, operation_id, event_type, agent_name,
             json.dumps(event_data), checksum),
        )
    conn.commit()
    return {"logged": True, "record_id": record_id, "checksum": checksum}


@server.tool("log_tool_call")
def log_tool_call(
    operation_id: str,
    agent_name: str,
    tool_name: str,
    input_hash: str,
    output_hash: str,
    timestamp: str,
) -> dict:
    conn = _get_conn()
    try:
        return _append(conn, operation_id, "TOOL_CALL", {
            "tool_name": tool_name,
            "input_hash": input_hash,
            "output_hash": output_hash,
            "timestamp": timestamp,
        }, agent_name=agent_name)
    finally:
        conn.close()


@server.tool("log_agent_output")
def log_agent_output(
    operation_id: str,
    agent_name: str,
    output_summary: str,
    handoff_contract_hash: str,
    timestamp: str,
) -> dict:
    conn = _get_conn()
    try:
        return _append(conn, operation_id, "AGENT_OUTPUT", {
            "output_summary": output_summary,
            "handoff_contract_hash": handoff_contract_hash,
            "timestamp": timestamp,
        }, agent_name=agent_name)
    finally:
        conn.close()


@server.tool("log_human_decision")
def log_human_decision(
    operation_id: str,
    decision: str,
    reason: str,
    approver_id: str,
    timestamp: str,
) -> dict:
    conn = _get_conn()
    try:
        return _append(conn, operation_id, "HUMAN_DECISION", {
            "decision": decision,
            "reason": reason,
            "approver_id": approver_id,
            "timestamp": timestamp,
        })
    finally:
        conn.close()


@server.tool("log_execution")
def log_execution(
    operation_id: str,
    execution_success: bool,
    actual_rows: int | None,
    blast_radius_delta: float | None,
    timestamp: str,
) -> dict:
    conn = _get_conn()
    try:
        return _append(conn, operation_id, "EXECUTION", {
            "execution_success": execution_success,
            "actual_rows": actual_rows,
            "blast_radius_delta": blast_radius_delta,
            "timestamp": timestamp,
        })
    finally:
        conn.close()


@server.tool("log_auto_reject")
def log_auto_reject(
    operation_id: str,
    rule_name: str,
    matched_pattern: str,
    timestamp: str,
) -> dict:
    conn = _get_conn()
    try:
        return _append(conn, operation_id, "AUTO_REJECT", {
            "rule_name": rule_name,
            "matched_pattern": matched_pattern,
            "timestamp": timestamp,
        })
    finally:
        conn.close()


@server.tool("list_operations")
def list_operations(
    tenant_id: str,
    limit: int = 50,
    offset: int = 0,
) -> dict:
    """
    List audit records for the given tenant.
    Requires a separate read-capable connection (audit_reader role).
    """
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, operation_id, event_type, agent_name, logged_at, checksum
                FROM contragate_app.audit_log
                WHERE tenant_id = %s
                ORDER BY logged_at DESC
                LIMIT %s OFFSET %s
                """,
                (tenant_id, limit, offset),
            )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                if r.get("logged_at"):
                    r["logged_at"] = r["logged_at"].isoformat()
        return {"records": rows, "count": len(rows)}
    finally:
        conn.close()


@server.tool("verify_checksum")
def verify_checksum(record_id: str) -> dict:
    """
    Verify the integrity of an audit record by recomputing its checksum.
    Returns True if the stored checksum matches the recomputed value.
    """
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT id, operation_id, event_type, agent_name, event_data, checksum
                FROM contragate_app.audit_log
                WHERE id = %s
                """,
                (record_id,),
            )
            row = cur.fetchone()
        if not row:
            return {"valid": False, "error": "Record not found"}

        computed = _checksum({
            "id": str(row["id"]),
            "operation_id": row["operation_id"],
            "event_type": row["event_type"],
            "agent_name": row["agent_name"],
            "event_data": row["event_data"],
        })
        valid = computed == row["checksum"]
        return {"valid": valid, "stored_checksum": row["checksum"], "computed_checksum": computed}
    finally:
        conn.close()


if __name__ == "__main__":
    server.run()
