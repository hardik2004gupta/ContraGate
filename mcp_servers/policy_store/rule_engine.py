"""
Deterministic policy rule engine — pure function, no database access.

evaluate_operation() takes all inputs as arguments.
The policy-store MCP server fetches policy rules from the database and
calls evaluate_operation(). Unit tests call evaluate_operation() directly
without needing a database connection.

Per CLAUDE.md §22 invariant 2: An LLM must never override a hard policy rule.
This function is entirely deterministic and reproducible — no LLM, no DB.
"""
from __future__ import annotations


_SENSITIVE_TABLES: frozenset[str] = frozenset({"users", "orders", "invoices"})
_BULK_ROW_THRESHOLD = 10_000
_PII_EXPLAIN_COST_THRESHOLD = 100_000.0


def evaluate_operation(
    operation_type: str,
    tables: list[str],
    estimated_rows: int,
    source_type: str,
    has_external_triggers: bool,
    submission_hour: int,
    explain_cost: float,
    pii_tables: set[str],
    all_rules: list[dict],
    business_hours: tuple[int, int] = (7, 20),
) -> dict:
    """
    Evaluate an operation against a list of active policy rules.

    All inputs are pre-fetched by the caller (policy_store MCP server).
    Returns a dict with triggered_rules, has_hard_violation, required_tier, etc.

    Rule matching is strictly deterministic — same inputs always produce the same output.
    """
    triggered: list[dict] = []
    has_hard_violation = False
    has_soft_violation = False
    required_tier: str | None = None
    auto_reject_reason: str | None = None

    tables_set = set(tables)
    pii_involved = bool(tables_set & pii_tables)
    sensitive_involved = bool(tables_set & _SENSITIVE_TABLES)

    biz_start, biz_end = business_hours

    for rule in all_rules:
        rule_id: str = rule["rule_id"]
        severity: str = rule.get("severity", "SOFT")
        action: str = rule.get("action", "REQUIRE_REVIEW")
        description: str = rule.get("description", rule.get("rule_name", rule_id))
        cond = rule.get("condition") or {}

        matched = _match_rule(
            rule_id=rule_id,
            cond=cond,
            operation_type=operation_type,
            tables_set=tables_set,
            pii_involved=pii_involved,
            sensitive_involved=sensitive_involved,
            estimated_rows=estimated_rows,
            source_type=source_type,
            has_external_triggers=has_external_triggers,
            submission_hour=submission_hour,
            explain_cost=explain_cost,
            biz_start=biz_start,
            biz_end=biz_end,
        )

        if matched:
            triggered.append({
                "rule_id": rule_id,
                "rule_name": rule.get("rule_name", rule_id),
                "severity": severity,
                "description": description,
                "action": action,
            })
            if severity == "HARD":
                has_hard_violation = True
                if auto_reject_reason is None:
                    auto_reject_reason = description
            else:
                has_soft_violation = True
            if action == "REQUIRE_FULL_CONTRACT":
                required_tier = "FULL_CONTRACT"
            elif action == "REQUIRE_STANDARD_REVIEW" and required_tier is None:
                required_tier = "STANDARD"

    return {
        "triggered_rules": triggered,
        "has_hard_violation": has_hard_violation,
        "has_soft_violation": has_soft_violation,
        "required_tier": required_tier,
        "auto_reject_reason": auto_reject_reason,
    }


def _match_rule(
    *,
    rule_id: str,
    cond: dict,
    operation_type: str,
    tables_set: set[str],
    pii_involved: bool,
    sensitive_involved: bool,
    estimated_rows: int,
    source_type: str,
    has_external_triggers: bool,
    submission_hour: int,
    explain_cost: float,
    biz_start: int,
    biz_end: int,
) -> bool:
    if rule_id == "POLICY_DDL_NO_BACKUP":
        return operation_type in ("DROP", "TRUNCATE")

    if rule_id == "POLICY_PII_STANDARD_REVIEW":
        return pii_involved

    if rule_id == "POLICY_EXTERNAL_INPUT":
        return source_type == "external_user_input"

    if rule_id == "POLICY_BULK_DELETE_SENSITIVE":
        return (
            operation_type in ("DELETE", "UPDATE")
            and sensitive_involved
            and estimated_rows > _BULK_ROW_THRESHOLD
        )

    if rule_id == "POLICY_PAYMENT_WEBHOOK":
        return has_external_triggers

    if rule_id == "POLICY_AFTER_HOURS":
        start = cond.get("business_hours_start", biz_start) if isinstance(cond, dict) else biz_start
        end = cond.get("business_hours_end", biz_end) if isinstance(cond, dict) else biz_end
        return not (start <= submission_hour < end)

    if rule_id == "POLICY_AUTO_REJECT_PATTERN":
        # Evaluated externally via memory_store.check_auto_reject_pattern;
        # not matched here (requires async DB lookup).
        return False

    if rule_id == "POLICY_PII_EXPENSIVE_READ":
        return (
            operation_type == "SELECT"
            and pii_involved
            and explain_cost > _PII_EXPLAIN_COST_THRESHOLD
        )

    # Unknown rule — fail open (do not auto-reject for rules we don't recognize)
    return False
