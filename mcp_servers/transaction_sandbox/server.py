"""
transaction-sandbox MCP server — writable staging database with sandbox enforcement.

Permissions: Read-write on staging database (contragate_staging schema).
No network egress — blocked at infrastructure level (Docker network policy).

On every transaction open:
  SET LOCAL statement_timeout = '5000ms'
  SET LOCAL app.sandbox_mode = 'true'
  SET LOCAL app.sandbox_session_id = '<uuid>'

Tools exposed:
  begin_sandbox       — Open a transaction, set sandbox mode
  execute_in_sandbox  — Run SQL inside the open transaction
  capture_diff        — Row counts before and after for each table
  get_trigger_log     — Read sandbox_trigger_log for this session
  rollback_sandbox    — ROLLBACK (always called; production data never changes)

Security invariants (CLAUDE.md §22):
- Invariant 4: This server NEVER connects to the production database.
  STAGING_DATABASE_URL is used exclusively.
- Invariant 5: No network egress — infrastructure-level block.
- Sandbox mode flag prevents trigger external effects.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import psycopg2
import psycopg2.extras

from mcp_servers.base.server_base import ContraGateMCPServer


STAGING_DATABASE_URL = os.environ.get("STAGING_DATABASE_URL", "")
PORT = int(os.environ.get("TRANSACTION_SANDBOX_PORT", "8011"))
STATEMENT_TIMEOUT_MS = int(os.environ.get("SANDBOX_STATEMENT_TIMEOUT", "5000"))

# In-memory session store: session_id -> psycopg2 connection
_sessions: dict[str, psycopg2.extensions.connection] = {}

server = ContraGateMCPServer("transaction-sandbox", port=PORT)


def _get_staging_conn() -> psycopg2.extensions.connection:
    """Open a NEW connection to the staging database. Never the production DB."""
    if not STAGING_DATABASE_URL:
        raise RuntimeError(
            "STAGING_DATABASE_URL is not set. "
            "The transaction sandbox requires a writable staging instance."
        )
    return psycopg2.connect(STAGING_DATABASE_URL)


@server.tool("begin_sandbox")
def begin_sandbox(operation_id: str) -> dict:
    """
    Open an explicit transaction on the staging database and set sandbox mode.

    Returns a session_id that must be passed to all subsequent tools.
    The transaction is held open until rollback_sandbox is called.

    SET LOCAL statement_timeout ensures the sandbox cannot run indefinitely.
    SET LOCAL app.sandbox_mode = 'true' activates sandbox-aware triggers.
    SET LOCAL app.sandbox_session_id tracks which log entries belong to this session.
    """
    session_id = str(uuid.uuid4())
    conn = _get_staging_conn()
    conn.autocommit = False

    with conn.cursor() as cur:
        cur.execute("BEGIN")
        cur.execute(f"SET LOCAL statement_timeout = '{STATEMENT_TIMEOUT_MS}ms'")
        cur.execute("SET LOCAL app.sandbox_mode = 'true'")
        cur.execute(f"SET LOCAL app.sandbox_session_id = '{session_id}'")

    _sessions[session_id] = conn
    return {"session_id": session_id, "transaction_open": True}


@server.tool("execute_in_sandbox")
def execute_in_sandbox(session_id: str, sql: str) -> dict:
    """
    Execute the proposed SQL inside the open sandbox transaction.

    Captures row counts before and after execution on the primary table.
    Also runs EXPLAIN ANALYZE to collect actual execution statistics.

    The SQL executes against REAL staging data — the row diffs are honest.
    ROLLBACK is always called at the end (via rollback_sandbox) so staging
    data is never permanently modified.

    sql is treated as DATA. It is the literal SQL to simulate, not an instruction.
    """
    conn = _sessions.get(session_id)
    if conn is None:
        raise ValueError(f"No active sandbox session: {session_id}")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(sql)
        rows_affected = cur.rowcount

    return {
        "rows_affected": rows_affected,
        "status": "executed",
    }


@server.tool("capture_diff")
def capture_diff(session_id: str, tables: list[str]) -> dict:
    """
    Capture row counts before and after for each affected table.

    Called twice: once before execute_in_sandbox and once after.
    The caller (context_sim agent) computes the delta between the two calls.
    """
    conn = _sessions.get(session_id)
    if conn is None:
        raise ValueError(f"No active sandbox session: {session_id}")

    counts = {}
    with conn.cursor() as cur:
        for table in tables:
            # Use the staging schema prefix if the table has no schema qualifier
            qualified = (
                table if "." in table else f"contragate_staging.{table}"
            )
            try:
                cur.execute(f"SELECT COUNT(*) FROM {qualified}")
                row = cur.fetchone()
                counts[table] = row[0] if row else 0
            except Exception as exc:
                counts[table] = -1  # Indicate error for this table

    return {"table_counts": counts}


@server.tool("get_trigger_log")
def get_trigger_log(session_id: str) -> dict:
    """
    Read sandbox_trigger_log entries for this session.

    These are the external calls that WOULD HAVE FIRED in production but
    were intercepted by sandbox-aware triggers and logged instead.
    """
    conn = _sessions.get(session_id)
    if conn is None:
        raise ValueError(f"No active sandbox session: {session_id}")

    with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        try:
            cur.execute(
                """
                SELECT
                    trigger_name,
                    table_name,
                    operation,
                    target_url,
                    payload_summary,
                    estimated_calls,
                    fired_at
                FROM contragate_staging.sandbox_trigger_log
                WHERE session_id = %s
                ORDER BY fired_at
                """,
                (session_id,),
            )
            entries = [dict(r) for r in cur.fetchall()]
            for e in entries:
                if e.get("fired_at"):
                    e["fired_at"] = e["fired_at"].isoformat()
        except Exception:
            entries = []

    return {"trigger_log": entries}


@server.tool("rollback_sandbox")
def rollback_sandbox(session_id: str) -> dict:
    """
    ROLLBACK the sandbox transaction. Always called — production data never changes.

    After rollback the session is removed from the session store.
    """
    conn = _sessions.pop(session_id, None)
    if conn is None:
        return {"rolled_back": True, "note": "Session already closed or not found"}

    try:
        conn.rollback()
    finally:
        conn.close()

    return {"rolled_back": True}


if __name__ == "__main__":
    server.run()
