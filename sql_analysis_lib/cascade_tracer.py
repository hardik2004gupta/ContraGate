"""
FK cascade graph construction from information_schema.

Given a root table and WHERE condition, recursively traces all dependent
tables reachable via ON DELETE CASCADE foreign keys up to max_depth levels.

Root row count: uses row_estimator.estimate_rows(table, condition, conn) so
that condition selectivity (e.g., "WHERE last_active < NOW() - INTERVAL '2 years'")
propagates correctly to downstream cascade estimates. Callers that have already
computed a RowEstimate may pass it via root_estimate instead.

Composite FK support: joins information_schema.key_column_usage on both sides
using position_in_unique_constraint = ordinal_position to correctly align
multi-column foreign keys without column cross-pairing.

Circular FK reference detection uses a per-branch visited set. Reaching
a table already in the current traversal path sets has_circular_reference.

SECURITY: all table name lookups are parameterized. No table names are
interpolated raw into executable SQL.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .row_estimator import RowEstimate, estimate_rows


_DEFAULT_MAX_DEPTH = 5


@dataclass
class CascadeLevel:
    table_name: str
    column_name: str
    parent_table: str
    parent_column: str
    cascade_action: str  # "CASCADE" | "SET NULL" | "RESTRICT" | "NO ACTION"
    estimated_rows: int
    confidence: float
    depth: int


@dataclass
class CascadeGraph:
    root_table: str
    root_condition: str
    levels: list[CascadeLevel]
    total_estimated_rows: int
    has_circular_reference: bool
    max_depth_reached: bool


def _get_reltuples(table: str, conn: Any) -> int:
    """Fetch reltuples estimate for a table from pg_class."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.reltuples::bigint
            FROM pg_class c
            JOIN pg_namespace n ON n.oid = c.relnamespace
            WHERE c.relname = %s
              AND n.nspname = 'public'
            """,
            (table,),
        )
        row = cur.fetchone()
    if row is None:
        return 0
    return max(0, int(row[0] if not isinstance(row, dict) else row["reltuples"]))


def _get_cascade_children(parent_table: str, conn: Any) -> list[dict[str, str]]:
    """
    Return immediate FK children of parent_table that have ON DELETE CASCADE.

    Uses ordinal_position / position_in_unique_constraint alignment to correctly
    handle composite foreign keys (multi-column FKs). Without this alignment,
    a JOIN between key_column_usage and constraint_column_usage can cross-pair
    columns of a composite key.

    Returns list of dicts: child_table, child_column, parent_column, cascade_action.
    Only CASCADE action rows are returned.
    """
    query = """
        SELECT
            kcu_fk.table_name    AS child_table,
            kcu_fk.column_name   AS child_column,
            kcu_pk.column_name   AS parent_column,
            rc.delete_rule       AS cascade_action
        FROM information_schema.referential_constraints rc
        JOIN information_schema.key_column_usage kcu_fk
            ON  rc.constraint_name   = kcu_fk.constraint_name
            AND rc.constraint_schema = kcu_fk.constraint_schema
        JOIN information_schema.key_column_usage kcu_pk
            ON  rc.unique_constraint_name   = kcu_pk.constraint_name
            AND rc.unique_constraint_schema = kcu_pk.constraint_schema
            AND kcu_fk.position_in_unique_constraint = kcu_pk.ordinal_position
        WHERE kcu_pk.table_name = %s
          AND rc.delete_rule = 'CASCADE'
        ORDER BY kcu_fk.table_name, kcu_fk.ordinal_position
    """
    with conn.cursor() as cur:
        cur.execute(query, (parent_table,))
        rows = cur.fetchall()

    def _val(row: Any, key: str, idx: int) -> str:
        return row[key] if isinstance(row, dict) else row[idx]

    return [
        {
            "child_table": _val(r, "child_table", 0),
            "child_column": _val(r, "child_column", 1),
            "parent_column": _val(r, "parent_column", 2),
            "cascade_action": _val(r, "cascade_action", 3),
        }
        for r in rows
    ]


def _trace(
    table: str,
    parent_rows: int,
    parent_confidence: float,
    depth: int,
    max_depth: int,
    path: set[str],
    conn: Any,
    levels: list[CascadeLevel],
    flags: dict[str, bool],
) -> None:
    """Recursive DFS traversal of the FK cascade graph."""
    if depth > max_depth:
        flags["max_depth_reached"] = True
        return

    children = _get_cascade_children(table, conn)
    parent_reltuples = _get_reltuples(table, conn)

    for child in children:
        child_table = child["child_table"]

        if child_table in path:
            flags["has_circular_reference"] = True
            continue

        child_reltuples = _get_reltuples(child_table, conn)
        if parent_reltuples > 0:
            ratio = child_reltuples / parent_reltuples
        else:
            ratio = 0.0

        child_estimated = int(parent_rows * ratio)
        child_confidence = round(parent_confidence * 0.8, 4)

        levels.append(CascadeLevel(
            table_name=child_table,
            column_name=child["child_column"],
            parent_table=table,
            parent_column=child["parent_column"],
            cascade_action=child["cascade_action"],
            estimated_rows=child_estimated,
            confidence=child_confidence,
            depth=depth,
        ))

        _trace(
            table=child_table,
            parent_rows=child_estimated,
            parent_confidence=child_confidence,
            depth=depth + 1,
            max_depth=max_depth,
            path=path | {child_table},
            conn=conn,
            levels=levels,
            flags=flags,
        )


def trace_cascades(
    table: str,
    condition: str,
    conn: Any,
    max_depth: int = _DEFAULT_MAX_DEPTH,
    root_estimate: RowEstimate | None = None,
) -> CascadeGraph:
    """
    Build the full cascade graph for an operation on *table*.

    condition is the WHERE clause already extracted by ContraGate's SQL parser
    (internal, trusted input — not raw user text).

    root_estimate: if the caller has already computed a RowEstimate (e.g., the
    Analyzer Agent's count_affected_rows tool), pass it here to avoid a redundant
    EXPLAIN call. When None, estimate_rows(table, condition, conn) is called
    internally so that condition selectivity propagates to cascade estimates.

    Returns a CascadeGraph with a flat list of all cascade levels in DFS order,
    total estimated rows across all levels, and flags for circular references
    and depth-limit hits.
    """
    if root_estimate is None:
        root_estimate = estimate_rows(table, condition, conn)

    root_rows = root_estimate.estimated_rows
    root_confidence = root_estimate.confidence

    levels: list[CascadeLevel] = []
    flags: dict[str, bool] = {
        "has_circular_reference": False,
        "max_depth_reached": False,
    }

    _trace(
        table=table,
        parent_rows=root_rows,
        parent_confidence=root_confidence,
        depth=1,
        max_depth=max_depth,
        path={table},
        conn=conn,
        levels=levels,
        flags=flags,
    )

    total_cascade_rows = sum(lvl.estimated_rows for lvl in levels)

    return CascadeGraph(
        root_table=table,
        root_condition=condition,
        levels=levels,
        total_estimated_rows=total_cascade_rows,
        has_circular_reference=flags["has_circular_reference"],
        max_depth_reached=flags["max_depth_reached"],
    )
