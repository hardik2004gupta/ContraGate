"""
Deterministic reversibility classification — pure function, no LLM, no DB.

Takes structured inputs (operation type, table metadata, trigger analysis,
PITR availability) and returns one of four classifications.

Priority order (first match wins, per CLAUDE.md §13 and architecture PDF):
  1. DDL (DROP, TRUNCATE, ALTER, CREATE INDEX, CREATE TABLE) → PERMANENT (always)
  2. Non-transactional trigger side effects + permanent DB change → PERMANENT
  3. Non-transactional trigger side effects + reversible DB change → PARTIAL
  4. DELETE/UPDATE, no soft-delete column, no confirmed PITR → PERMANENT
  5. DELETE/UPDATE with soft-delete column → REVERSIBLE_AUTOMATED
  6. DELETE/UPDATE with confirmed PITR (valid window_hours) → REVERSIBLE_PITR
  7. INSERT (no trigger) → REVERSIBLE_AUTOMATED
  8. SELECT / READ (no trigger) → REVERSIBLE_AUTOMATED
  9. Unknown operation type → ValueError (fail closed — never REVERSIBLE_AUTOMATED)

ABSOLUTE PROHIBITION (CLAUDE.md §22, invariant 2; ADR-008):
  No LLM involvement in this classification.
  No LLM-generated rollback SQL is produced here or elsewhere.

PITR validation:
  pitr_confirmed=True requires pitr_window_hours to be a positive number.
  Passing None or a non-positive value with pitr_confirmed=True raises ValueError.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .trigger_detector import TriggerAnalysis


_DDL_OPERATIONS = frozenset({
    "DROP", "TRUNCATE", "ALTER", "CREATE", "DDL",
    "drop", "truncate", "alter", "create", "ddl",
})

_KNOWN_DML_OPERATIONS = frozenset({
    "SELECT", "INSERT", "UPDATE", "DELETE",
    "READ",  # alias for SELECT in some internal representations
    "select", "insert", "update", "delete", "read",
})


class ReversibilityClass(Enum):
    REVERSIBLE_AUTOMATED = "REVERSIBLE_AUTOMATED"
    REVERSIBLE_PITR = "REVERSIBLE_PITR"
    PARTIAL = "PARTIAL"
    PERMANENT = "PERMANENT"


@dataclass
class ReversibilityResult:
    classification: ReversibilityClass
    reason: str
    automated_recovery_sql: str | None  # Always None per ADR-008
    pitr_available: bool
    pitr_window_hours: float | None
    permanent_components: list[str] = field(default_factory=list)


def classify_reversibility(
    operation_type: str,
    table: str,
    has_soft_delete_column: bool,
    trigger_analysis: "TriggerAnalysis",
    pitr_confirmed: bool,
    pitr_window_hours: float | None,
    estimated_rows: int,
) -> ReversibilityResult:
    """
    Classify the reversibility of an operation deterministically.

    operation_type: "SELECT" | "INSERT" | "UPDATE" | "DELETE" | "DDL"
                    or specific DDL verbs: "DROP" | "TRUNCATE" | "ALTER" | "CREATE"
                    (case-insensitive)
    table: target table name (used in reason strings and component labels)
    has_soft_delete_column: True when table has a deleted_at / is_deleted style column
    trigger_analysis: result of trigger_detector.detect_triggers()
    pitr_confirmed: True when the database has confirmed PITR snapshot available
    pitr_window_hours: PITR window size in hours; must be a positive number when
                       pitr_confirmed=True, else raises ValueError
    estimated_rows: row count estimate (used in reason strings for context)

    Raises:
      ValueError: if pitr_confirmed=True but pitr_window_hours is None or <= 0
      ValueError: if operation_type is not a recognized DDL or DML operation
                  (fail closed — unknown operations never receive REVERSIBLE_AUTOMATED)
    """

    # ── PITR input validation ────────────────────────────────────────────────
    if pitr_confirmed:
        if pitr_window_hours is None or pitr_window_hours <= 0:
            raise ValueError(
                f"pitr_confirmed=True requires pitr_window_hours to be a positive "
                f"number of hours, got {pitr_window_hours!r}. "
                f"A zero or None window cannot guarantee point-in-time recovery."
            )

    permanent_components: list[str] = []

    # ── Priority 1: DDL always PERMANENT ────────────────────────────────────
    if operation_type in _DDL_OPERATIONS:
        return ReversibilityResult(
            classification=ReversibilityClass.PERMANENT,
            reason=f"DDL operation ({operation_type}) is irreversible; no automated recovery possible",
            automated_recovery_sql=None,
            pitr_available=pitr_confirmed,
            pitr_window_hours=pitr_window_hours,
            permanent_components=[operation_type.upper()],
        )

    # Collect non-transactional trigger effects
    has_nt_effects = trigger_analysis.has_permanent_side_effects
    nt_triggers = trigger_analysis.non_transactional_triggers
    for t in nt_triggers:
        permanent_components.append(
            f"trigger:{t.trigger_name}:{t.non_transactional_extension or 'unknown'}"
        )

    # ── Classify the DB change in isolation ─────────────────────────────────
    op = operation_type.upper()

    if op in ("SELECT", "READ"):
        db_class: ReversibilityClass | None = ReversibilityClass.REVERSIBLE_AUTOMATED
        db_reason = "Read-only operation; no data modification"

    elif op == "INSERT":
        db_class = ReversibilityClass.REVERSIBLE_AUTOMATED
        db_reason = f"INSERT rows can be deleted by primary key ({estimated_rows} row(s) estimated)"

    elif op in ("DELETE", "UPDATE"):
        if has_soft_delete_column:
            db_class = ReversibilityClass.REVERSIBLE_AUTOMATED
            db_reason = (
                f"Soft-delete column present; rows can be restored "
                f"by updating deleted_at/is_deleted ({estimated_rows} row(s))"
            )
        elif pitr_confirmed:
            db_class = ReversibilityClass.REVERSIBLE_PITR
            db_reason = (
                f"PITR snapshot available (window: {pitr_window_hours}h); "
                f"restore via point-in-time recovery"
            )
        else:
            # Priority 4: no soft-delete, no PITR → PERMANENT
            db_class = None  # sentinel for "DB change is permanent"
            permanent_components.append(f"no_recovery_path_for_{table}")

    elif operation_type in _KNOWN_DML_OPERATIONS:
        # Catch any remaining known DML forms that aren't handled above
        # (e.g., lowercase variants not covered by op checks — shouldn't occur)
        db_class = ReversibilityClass.REVERSIBLE_AUTOMATED
        db_reason = f"Known DML operation ({operation_type}) classified as reversible"

    else:
        # Unknown operation type — fail closed.
        # NEVER return REVERSIBLE_AUTOMATED for unrecognized operations.
        # The caller (Analyzer Agent) must normalize the operation type before calling.
        raise ValueError(
            f"Unknown operation type '{operation_type}'. "
            f"Cannot safely classify reversibility for unrecognized operations — "
            f"failing closed to prevent false REVERSIBLE_AUTOMATED classifications. "
            f"Recognized types: SELECT, INSERT, UPDATE, DELETE, "
            f"DROP, TRUNCATE, ALTER, CREATE, DDL (and case variants)."
        )

    db_permanent = db_class is None

    # ── Combine DB classification with trigger effects ───────────────────────

    if db_permanent and has_nt_effects:
        # Priorities 2+3 combined: both DB and trigger effects are permanent
        return ReversibilityResult(
            classification=ReversibilityClass.PERMANENT,
            reason=(
                f"No recovery path for {op} on {table} "
                f"({estimated_rows} row(s)); additionally {len(nt_triggers)} "
                f"non-transactional trigger(s) produce permanent external effects"
            ),
            automated_recovery_sql=None,
            pitr_available=pitr_confirmed,
            pitr_window_hours=pitr_window_hours,
            permanent_components=permanent_components,
        )

    if db_permanent:
        return ReversibilityResult(
            classification=ReversibilityClass.PERMANENT,
            reason=f"No recovery path for {op} on {table} ({estimated_rows} row(s))",
            automated_recovery_sql=None,
            pitr_available=pitr_confirmed,
            pitr_window_hours=pitr_window_hours,
            permanent_components=permanent_components,
        )

    if has_nt_effects:
        # DB change is reversible BUT triggers cause permanent external effects → PARTIAL
        return ReversibilityResult(
            classification=ReversibilityClass.PARTIAL,
            reason=(
                f"Database change is {db_class.value} but "  # type: ignore[union-attr]
                f"{len(nt_triggers)} trigger(s) cause permanent external effects "
                f"({', '.join(t.non_transactional_extension or '?' for t in nt_triggers)})"
            ),
            automated_recovery_sql=None,
            pitr_available=pitr_confirmed,
            pitr_window_hours=pitr_window_hours,
            permanent_components=permanent_components,
        )

    # Pure reversible case (no triggers, DB change is reversible)
    return ReversibilityResult(
        classification=db_class,  # type: ignore[arg-type]
        reason=db_reason,
        automated_recovery_sql=None,  # ADR-008: LLM-generated rollback SQL is prohibited
        pitr_available=pitr_confirmed,
        pitr_window_hours=pitr_window_hours,
        permanent_components=[],
    )
