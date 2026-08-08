"""
EXPLAIN (FORMAT JSON) output parser.

Pure module — no database connection required. Input is the already-fetched
JSON dict (or list) from psycopg2; output is a typed ExplainResult dataclass.

Used by:
  - Read Impact Gate (proxy/risk_gate.py): total_cost for routing threshold
  - row_estimator.py: estimated_rows for conditional estimates
  - Context+Sim Agent (sandbox.py): actual_rows after ANALYZE execution

Security: run_explain() validates that the sql argument is a single SELECT
statement before wrapping it in EXPLAIN. Multi-statement payloads (e.g.,
"SELECT 1; DELETE FROM users") are rejected before reaching the database.
SQL is treated as DATA, not as instructions.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


@dataclass
class ExplainResult:
    estimated_rows: int
    actual_rows: int | None
    total_cost: float
    startup_cost: float
    scan_type: str
    partitions_involved: int
    shared_hit_blocks: int | None
    shared_read_blocks: int | None
    execution_time_ms: float | None
    planning_time_ms: float | None


# Statement types permitted inside EXPLAIN
_ALLOWED_STATEMENT_TYPES = frozenset({
    "SELECT", "TABLE", "VALUES",
    # EXPLAIN of EXPLAIN is unusual but not dangerous
    "EXPLAIN",
    # WITH ... SELECT (CTE) — first token is WITH
    "WITH",
})

# Pattern to strip single-quoted string literals (handles escaped quotes via '')
_SINGLE_QUOTED = re.compile(r"'(?:[^']|'')*'", re.DOTALL)
# Pattern to strip dollar-quoted strings (common in PostgreSQL function defs)
_DOLLAR_QUOTED = re.compile(r"\$(?P<tag>[^$]*)\$.*?\$(?P=tag)\$", re.DOTALL)
# Pattern to strip single-line comments
_LINE_COMMENTS = re.compile(r"--[^\n]*")
# Pattern to strip block comments
_BLOCK_COMMENTS = re.compile(r"/\*.*?\*/", re.DOTALL)
# Pattern to strip double-quoted identifiers
_DOUBLE_QUOTED = re.compile(r'"(?:[^"\\]|\\.)*"')


def _strip_sql_literals_and_comments(sql: str) -> str:
    """
    Remove string literals and comments from sql, replacing with whitespace.

    This is used solely to locate statement boundaries (semicolons) and the
    leading keyword — it does NOT parse the SQL fully.
    """
    s = _DOLLAR_QUOTED.sub(" ", sql)
    s = _BLOCK_COMMENTS.sub(" ", s)
    s = _LINE_COMMENTS.sub(" ", s)
    s = _SINGLE_QUOTED.sub(" ", s)
    s = _DOUBLE_QUOTED.sub(" ", s)
    return s


def _validate_explain_target(sql: str) -> None:
    """
    Validate that sql is safe to wrap in EXPLAIN (FORMAT JSON) {sql}.

    Rules:
      1. Must be a single statement — no semicolons outside literals/comments.
      2. Statement type must be SELECT (or WITH/TABLE/VALUES for equivalent forms).

    Raises ValueError on violation. This prevents multi-statement SQL injection
    through the EXPLAIN wrapper (e.g., "SELECT 1; DELETE FROM users").

    SQL is treated as DATA throughout. This function never sends sql to an LLM.
    """
    stripped = _strip_sql_literals_and_comments(sql)

    if ";" in stripped:
        raise ValueError(
            "sql passed to run_explain() must be a single statement — "
            "semicolons are not permitted. Multi-statement payloads could "
            "execute statements beyond the EXPLAIN boundary. "
            f"Received sql starting with: {sql[:80]!r}"
        )

    tokens = stripped.split()
    if not tokens:
        raise ValueError("sql passed to run_explain() is empty")

    first = tokens[0].upper()
    if first not in _ALLOWED_STATEMENT_TYPES:
        raise ValueError(
            f"sql passed to run_explain() must be a SELECT statement. "
            f"Got statement type '{first}'. "
            f"Only {sorted(_ALLOWED_STATEMENT_TYPES)} are permitted. "
            f"Received sql starting with: {sql[:80]!r}"
        )


def _count_partitions(plan_node: dict[str, Any]) -> int:
    """Recursively count partitions accessed via the first Append node encountered."""
    if plan_node.get("Node Type") == "Append":
        return len(plan_node.get("Plans", []))
    for child in plan_node.get("Plans", []):
        count = _count_partitions(child)
        if count > 0:
            return count
    return 0


def parse_explain_json(explain_output: dict[str, Any] | list[Any]) -> ExplainResult:
    """
    Parse PostgreSQL EXPLAIN (FORMAT JSON) output into an ExplainResult.

    Accepts either:
      - The raw psycopg2 result (list with one element): [{Plan: ..., Planning Time: ...}]
      - The first element dict directly: {Plan: ..., Planning Time: ...}

    Raises ValueError for malformed input (empty list, missing Plan key).
    """
    if isinstance(explain_output, list):
        if not explain_output:
            raise ValueError(
                "parse_explain_json received an empty list — "
                "EXPLAIN (FORMAT JSON) must return at least one plan element"
            )
        data = explain_output[0]
    else:
        data = explain_output

    if not isinstance(data, dict):
        raise ValueError(
            f"parse_explain_json expected a dict plan element, got {type(data).__name__}"
        )

    plan = data.get("Plan")
    if plan is None:
        raise ValueError(
            "parse_explain_json: missing 'Plan' key in EXPLAIN output. "
            f"Available keys: {list(data.keys())}"
        )
    if not isinstance(plan, dict):
        raise ValueError(
            f"parse_explain_json: 'Plan' must be a dict, got {type(plan).__name__}"
        )

    actual_rows: int | None = None
    if "Actual Rows" in plan:
        actual_rows = int(plan["Actual Rows"])

    shared_hit = plan.get("Shared Hit Blocks")
    shared_read = plan.get("Shared Read Blocks")

    return ExplainResult(
        estimated_rows=int(plan.get("Plan Rows", 0)),
        actual_rows=actual_rows,
        total_cost=float(plan.get("Total Cost", 0.0)),
        startup_cost=float(plan.get("Startup Cost", 0.0)),
        scan_type=plan.get("Node Type", "Unknown"),
        partitions_involved=_count_partitions(plan),
        shared_hit_blocks=int(shared_hit) if shared_hit is not None else None,
        shared_read_blocks=int(shared_read) if shared_read is not None else None,
        execution_time_ms=data.get("Execution Time"),
        planning_time_ms=data.get("Planning Time"),
    )


def run_explain(sql: str, conn: Any, analyze: bool = False) -> ExplainResult:
    """
    Run EXPLAIN (FORMAT JSON) against a live connection and return parsed result.

    sql must be a single SELECT statement. Multi-statement inputs and non-SELECT
    statement types are rejected before reaching the database.

    sql is treated as DATA — it is the literal query text to EXPLAIN, not
    interpreted as instructions. Never pass raw user-supplied text that has not
    been through the ContraGate SQL parser.

    With analyze=True also passes ANALYZE and BUFFERS, populating actual_rows
    and shared_hit_blocks / shared_read_blocks.

    Raises ValueError if sql fails statement validation.
    Raises RuntimeError if EXPLAIN returns no rows.
    """
    _validate_explain_target(sql)

    if analyze:
        explain_prefix = "EXPLAIN (FORMAT JSON, ANALYZE, BUFFERS)"
    else:
        explain_prefix = "EXPLAIN (FORMAT JSON)"

    with conn.cursor() as cur:
        cur.execute(f"{explain_prefix} {sql}")
        raw = cur.fetchone()

    if raw is None:
        raise RuntimeError("EXPLAIN returned no rows")

    if not isinstance(raw, dict):
        # Tuple-style cursor (DictCursor is fine too): first column is the plan
        result = raw[0]
    else:
        # RealDictCursor: PostgreSQL returns column "QUERY PLAN" (with a space).
        # Try all known case variants before falling back to first value.
        result = (
            raw.get("QUERY PLAN")
            or raw.get("query_plan")
            or raw.get("query plan")
            or next(iter(raw.values()))
        )

    if isinstance(result, str):
        result = json.loads(result)

    return parse_explain_json(result)
