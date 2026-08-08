"""
Row count estimation from pg_stat_user_tables and EXPLAIN.

Estimation strategy:
  - "stats": no condition → reltuples from pg_class (fast, no table scan)
  - "explain": condition provided → EXPLAIN (FORMAT JSON) Plan Rows
  - "stats" (fallback): EXPLAIN fails → reltuples used as fallback

Confidence degrades with statistics staleness. Post-execution feedback
is applied via update_confidence() by the orchestrator after sandbox runs.

SECURITY: table names are parameterized in metadata queries.
The condition argument is a pre-parsed SQL fragment from ContraGate's internal
SQL parser — it is NOT raw user input and must never be user-supplied text.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from psycopg2 import sql as pgsql

from .explain_parser import ExplainResult, run_explain


@dataclass
class RowEstimate:
    table: str
    condition: str | None
    estimated_rows: int
    confidence: float
    estimation_strategy: str  # "stats" | "explain" | "combined"
    stats_age_hours: float
    stats_stale: bool


_STALE_THRESHOLD_HOURS = 24.0
# Confidence adjustment threshold: if |delta| > 10%, adjust confidence
_ACCURACY_THRESHOLD = 0.10


def _compute_confidence(stats_age_hours: float | None) -> tuple[float, bool]:
    """Return (confidence, is_stale) based on how old the table statistics are."""
    if stats_age_hours is None:
        return 0.4, True
    if stats_age_hours < _STALE_THRESHOLD_HOURS:
        return 0.8, False
    if stats_age_hours < 72:
        return 0.7, True
    if stats_age_hours < 168:
        return 0.6, True
    return 0.5, True


def _fetch_stats(table: str, conn: Any) -> tuple[int, float | None]:
    """
    Fetch reltuples and statistics age for a table.

    Returns (reltuples, stats_age_hours). stats_age_hours is None when
    the table has never been analyzed.
    """
    query = """
        SELECT
            c.reltuples::bigint AS reltuples,
            EXTRACT(EPOCH FROM (
                NOW() - GREATEST(s.last_analyze, s.last_autoanalyze)
            )) / 3600.0 AS stats_age_hours
        FROM pg_class c
        JOIN pg_stat_user_tables s ON s.relid = c.oid
        WHERE s.relname = %s
          AND s.schemaname = 'public'
    """
    with conn.cursor() as cur:
        cur.execute(query, (table,))
        row = cur.fetchone()

    if row is None:
        return 0, None

    reltuples = int(row[0]) if not isinstance(row, dict) else int(row["reltuples"])
    age_raw = row[1] if not isinstance(row, dict) else row["stats_age_hours"]
    stats_age = float(age_raw) if age_raw is not None else None

    return max(0, reltuples), stats_age


def estimate_rows(
    table: str,
    condition: str | None,
    conn: Any,
) -> RowEstimate:
    """
    Estimate the number of rows affected by an operation on *table*.

    When condition is None, reltuples from pg_stat_user_tables is used
    (strategy: "stats").
    When condition is provided, EXPLAIN is run against a synthetic query
    to leverage the planner's selectivity estimates (strategy: "explain").
    If EXPLAIN fails, falls back to reltuples (strategy: "stats").

    The condition must originate from ContraGate's internal SQL parser,
    not from raw user input.
    """
    reltuples, stats_age = _fetch_stats(table, conn)
    confidence, stale = _compute_confidence(stats_age)

    if condition is None:
        return RowEstimate(
            table=table,
            condition=None,
            estimated_rows=max(0, reltuples),
            confidence=confidence,
            estimation_strategy="stats",
            stats_age_hours=stats_age if stats_age is not None else math.inf,
            stats_stale=stale,
        )

    # EXPLAIN-based estimate for conditional query.
    # Table name is quoted via psycopg2.sql — never interpolated raw.
    # The condition is from ContraGate's internal parser (trusted).
    explain_sql = pgsql.SQL("SELECT 1 FROM {} WHERE {}").format(
        pgsql.Identifier(table),
        pgsql.SQL(condition),
    ).as_string(conn)

    strategy: str
    estimated: int
    try:
        result: ExplainResult = run_explain(explain_sql, conn, analyze=False)
        estimated = result.estimated_rows
        strategy = "explain"
    except Exception:
        # Fall back to reltuples when EXPLAIN fails (e.g., table not found,
        # condition references unresolvable subquery, or EXPLAIN times out).
        estimated = reltuples
        strategy = "stats"

    return RowEstimate(
        table=table,
        condition=condition,
        estimated_rows=max(0, estimated),
        confidence=confidence,
        estimation_strategy=strategy,
        stats_age_hours=stats_age if stats_age is not None else math.inf,
        stats_stale=stale,
    )


def update_confidence(
    table: str,
    operation_type: str,
    actual_rows: int,
    estimated_rows: int,
    confidence_store: dict[str, float],
) -> float:
    """
    Adjust the stored confidence for a table based on post-execution accuracy delta.

    Called by the orchestrator's feedback loop after sandbox or production execution.

    Per ContraGate MVP §Post-Execution Feedback Loop:
      - Underestimate (actual > estimated * 1.10): lower confidence by 0.05
      - Overestimate  (actual < estimated * 0.90): lower confidence by 0.02
      - Within ±10% delta: no change

    Underestimates are treated as more severe because they systematically mislead
    approvers toward thinking operations are smaller than they are.

    Returns the new confidence value (clamped to [0.1, 1.0]).
    """
    key = f"{table}:{operation_type}"
    current = confidence_store.get(key, 0.8)

    if estimated_rows <= 0:
        # estimated_rows = 0 with any actual rows → infinite underestimate
        new_confidence = current - 0.05 if actual_rows > 0 else current
    elif actual_rows > estimated_rows * (1.0 + _ACCURACY_THRESHOLD):
        # Underestimate: actual exceeded estimate by more than 10%
        new_confidence = current - 0.05
    elif actual_rows < estimated_rows * (1.0 - _ACCURACY_THRESHOLD):
        # Overestimate: actual was below estimate by more than 10%
        new_confidence = current - 0.02
    else:
        new_confidence = current

    new_confidence = max(0.1, min(1.0, new_confidence))
    confidence_store[key] = new_confidence
    return new_confidence
