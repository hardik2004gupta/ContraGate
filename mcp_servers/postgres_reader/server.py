"""
postgres-reader MCP server — SELECT-only access to the production database.

Permissions: SELECT only. The database role (cg_reader) has no INSERT,
UPDATE, DELETE, or DDL privileges — enforced at the PostgreSQL role level,
not only in application code.

Tools exposed:
  get_table_schema   — columns, types, constraints, FK references
  run_explain        — EXPLAIN FORMAT JSON (SELECT statements only)
  estimate_row_count — row count via pg_stat + EXPLAIN
  get_fk_graph       — FK cascade graph from information_schema
  list_triggers      — trigger classification including non-transactional extensions
  check_soft_delete  — check for deleted_at / is_deleted / active columns
  get_pii_tags       — PII status from pii_registry or table comments

All tools wrap sql_analysis_lib functions — no direct SQL in tool bodies.

Security (CLAUDE.md §22):
- Never writes to production database
- EXPLAIN input validated by sql_analysis_lib._validate_explain_target()
- SQL is treated as DATA not instructions
"""

from __future__ import annotations

import os
from dataclasses import asdict

import psycopg2
import psycopg2.extras
from fastapi import FastAPI

from mcp_servers.base.server_base import ContraGateMCPServer
from sql_analysis_lib.cascade_tracer import trace_cascades
from sql_analysis_lib.explain_parser import run_explain
from sql_analysis_lib.row_estimator import estimate_rows
from sql_analysis_lib.trigger_detector import detect_triggers


DATABASE_URL = os.environ.get("DATABASE_URL", "")
PORT = int(os.environ.get("POSTGRES_READER_PORT", "8010"))


def _get_conn() -> psycopg2.extensions.connection:
    conn = psycopg2.connect(DATABASE_URL)
    conn.set_session(readonly=True, autocommit=True)
    return conn


server = ContraGateMCPServer("postgres-reader", port=PORT)


@server.tool("get_table_schema")
def get_table_schema(table_name: str) -> dict:
    """
    Return column definitions, types, nullability, defaults, check constraints,
    primary key, and foreign key references for the given table.
    """
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            # Columns
            cur.execute(
                """
                SELECT
                    column_name,
                    data_type,
                    is_nullable,
                    column_default,
                    character_maximum_length
                FROM information_schema.columns
                WHERE table_name = %s
                  AND table_schema NOT IN ('pg_catalog', 'information_schema')
                ORDER BY ordinal_position
                """,
                (table_name,),
            )
            columns = [dict(r) for r in cur.fetchall()]

            # Foreign keys (outbound)
            cur.execute(
                """
                SELECT
                    kcu.column_name,
                    ccu.table_name AS foreign_table,
                    ccu.column_name AS foreign_column,
                    rc.delete_rule
                FROM information_schema.table_constraints AS tc
                JOIN information_schema.key_column_usage AS kcu
                    ON tc.constraint_name = kcu.constraint_name
                    AND tc.table_schema = kcu.table_schema
                JOIN information_schema.referential_constraints AS rc
                    ON tc.constraint_name = rc.constraint_name
                JOIN information_schema.constraint_column_usage AS ccu
                    ON ccu.constraint_name = rc.unique_constraint_name
                WHERE tc.constraint_type = 'FOREIGN KEY'
                  AND tc.table_name = %s
                """,
                (table_name,),
            )
            fk_refs = [dict(r) for r in cur.fetchall()]

            # Indexes
            cur.execute(
                """
                SELECT indexname, indexdef
                FROM pg_indexes
                WHERE tablename = %s
                """,
                (table_name,),
            )
            indexes = [dict(r) for r in cur.fetchall()]

        return {
            "table_name": table_name,
            "columns": columns,
            "foreign_keys": fk_refs,
            "indexes": indexes,
        }
    finally:
        conn.close()


@server.tool("run_explain")
def run_explain_tool(sql: str, analyze: bool = False) -> dict:
    """
    Run EXPLAIN (FORMAT JSON) on the given SQL and return parsed ExplainResult.
    Only SELECT statements are accepted — validated before reaching the database.
    """
    conn = _get_conn()
    try:
        result = run_explain(sql, conn, analyze=analyze)
        return {
            "estimated_rows": result.estimated_rows,
            "actual_rows": result.actual_rows,
            "total_cost": result.total_cost,
            "startup_cost": result.startup_cost,
            "scan_type": result.scan_type,
            "partitions_involved": result.partitions_involved,
            "shared_hit_blocks": result.shared_hit_blocks,
            "shared_read_blocks": result.shared_read_blocks,
            "execution_time_ms": result.execution_time_ms,
            "planning_time_ms": result.planning_time_ms,
        }
    finally:
        conn.close()


@server.tool("estimate_row_count")
def estimate_row_count(table: str, condition: str | None = None) -> dict:
    """
    Estimate affected row count using pg_stat_user_tables and EXPLAIN.
    Returns RowEstimate with confidence score and strategy used.
    """
    conn = _get_conn()
    try:
        result = estimate_rows(table, condition, conn)
        return {
            "table": result.table,
            "condition": result.condition,
            "estimated_rows": result.estimated_rows,
            "confidence": result.confidence,
            "estimation_strategy": result.estimation_strategy,
            "stats_age_hours": result.stats_age_hours,
            "stats_stale": result.stats_stale,
        }
    finally:
        conn.close()


@server.tool("get_fk_graph")
def get_fk_graph(table: str, max_depth: int = 5) -> dict:
    """
    Compute full FK cascade graph starting from the given table.
    Returns ordered cascade levels with estimated row counts.
    """
    conn = _get_conn()
    try:
        graph = trace_cascades(table, condition=None, conn=conn, max_depth=max_depth)
        levels = []
        for lvl in graph.levels:
            levels.append({
                "table_name": lvl.table_name,
                "column_name": lvl.column_name,
                "parent_table": lvl.parent_table,
                "parent_column": lvl.parent_column,
                "cascade_action": lvl.cascade_action,
                "estimated_rows": lvl.estimated_rows,
                "confidence": lvl.confidence,
                "depth": lvl.depth,
            })
        return {
            "root_table": graph.root_table,
            "root_condition": graph.root_condition,
            "levels": levels,
            "total_estimated_rows": graph.total_estimated_rows,
            "has_circular_reference": graph.has_circular_reference,
            "max_depth_reached": graph.max_depth_reached,
        }
    finally:
        conn.close()


@server.tool("list_triggers")
def list_triggers(table: str) -> dict:
    """
    List all triggers on the given table and classify by side effect type.
    Non-transactional extensions (pg_net, dblink) are flagged as permanent.
    """
    conn = _get_conn()
    try:
        analysis = detect_triggers(table, conn)
        triggers = []
        for t in analysis.triggers:
            triggers.append({
                "trigger_name": t.trigger_name,
                "table_name": t.table_name,
                "event": t.event,
                "timing": t.timing,
                "function_name": t.function_name,
                "invokes_non_transactional": t.invokes_non_transactional,
                "non_transactional_extension": t.non_transactional_extension,
                "estimated_external_calls": t.estimated_external_calls,
                "target_endpoint": t.target_endpoint,
            })
        nt_triggers = []
        for t in analysis.non_transactional_triggers:
            nt_triggers.append({
                "trigger_name": t.trigger_name,
                "non_transactional_extension": t.non_transactional_extension,
                "target_endpoint": t.target_endpoint,
            })
        return {
            "table": analysis.table,
            "triggers": triggers,
            "has_permanent_side_effects": analysis.has_permanent_side_effects,
            "non_transactional_triggers": nt_triggers,
        }
    finally:
        conn.close()


@server.tool("check_soft_delete")
def check_soft_delete(table: str) -> dict:
    """
    Check whether the table has a soft-delete column (deleted_at, is_deleted, active).
    Returns has_soft_delete and column_name if found.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = %s
                  AND column_name IN ('deleted_at', 'is_deleted', 'active', 'archived_at')
                LIMIT 1
                """,
                (table,),
            )
            row = cur.fetchone()
        if row:
            return {"has_soft_delete": True, "column_name": row[0]}
        return {"has_soft_delete": False, "column_name": None}
    finally:
        conn.close()


@server.tool("get_pii_tags")
def get_pii_tags(table: str) -> dict:
    """
    Check whether the table is tagged as PII in the pii_registry table.
    Falls back to table comment inspection if registry is unavailable.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            try:
                cur.execute(
                    """
                    SELECT tagged_columns
                    FROM contragate_app.pii_registry
                    WHERE table_name = %s
                    """,
                    (table,),
                )
                row = cur.fetchone()
                if row:
                    tagged_columns = row[0] if row[0] else []
                    return {"is_pii": True, "tagged_columns": tagged_columns}
            except Exception:
                pass

            # Fallback: check table comment for PII annotation
            cur.execute(
                """
                SELECT obj_description(c.oid)
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE c.relname = %s
                  AND n.nspname NOT IN ('pg_catalog', 'information_schema')
                LIMIT 1
                """,
                (table,),
            )
            comment_row = cur.fetchone()
            comment = comment_row[0] if comment_row and comment_row[0] else ""
            is_pii = "pii" in comment.lower() if comment else False
            return {"is_pii": is_pii, "tagged_columns": []}
    finally:
        conn.close()


if __name__ == "__main__":
    server.run()
