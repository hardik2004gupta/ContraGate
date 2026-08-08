"""
CONTRACT state — deterministic risk tier classification + contract assembly.

The Contract Agent applies rule-based risk tier classification and assembles
the full consequence contract from HandoffContract fields.

Security invariant: An LLM must NEVER override a hard policy rule.
Risk tier classification is entirely deterministic code — no LLM output.

Phase 2 behavior: Uses deterministic rules + plain-text assembly.
Phase 5 behavior: LLM used only for natural language summarization of the
already-classified risk tier and structured fields.

Writes to contract: risk_tier, risk_score, intent_summary_prose,
rollback_plan, contract_assembled.
"""

from __future__ import annotations

import logging
from datetime import datetime

from orchestrator.handoff_schema import (
    HandoffContract,
    OperationType,
    ReversibilityClass,
    RiskTier,
)

logger = logging.getLogger(__name__)

# ── Thresholds matching CLAUDE.md §13 ─────────────────────────────────────────
_CASCADE_ROW_THRESHOLD = 50_000
_EXTERNAL_TRIGGER_THRESHOLD = 5
_HIGH_RISK_SCORE = 0.7
_HIGH_UNCERTAINTY_SCORE = 0.75


async def run_contract(contract: HandoffContract) -> HandoffContract:
    """
    Apply deterministic risk tier rules and assemble consequence contract.
    """
    start = datetime.utcnow()

    # ── Step 1: Deterministic risk tier classification ───────────────────────
    contract.risk_tier = _classify_risk_tier(contract)

    # ── Step 2: Refine risk score with full analysis data ────────────────────
    contract.risk_score = _compute_final_risk_score(contract)

    # ── Step 3: Generate rollback plan (mechanical derivation only) ──────────
    contract.rollback_plan = _derive_rollback_plan(contract)

    # ── Step 4: Assemble human-readable consequence summary ──────────────────
    contract.intent_summary_prose = _assemble_prose_summary(contract)

    contract.contract_assembled = True

    elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
    contract.add_provenance(
        agent="CONTRACT_STUB",
        field_written=(
            "risk_tier,risk_score,rollback_plan,intent_summary_prose,contract_assembled"
        ),
        llm_involved=False,
    )

    logger.info(
        "CONTRACT complete",
        extra={
            "operation_id": contract.operation_id,
            "risk_tier": contract.risk_tier.value,
            "risk_score": contract.risk_score,
            "elapsed_ms": round(elapsed_ms, 1),
        },
    )
    return contract


def _classify_risk_tier(contract: HandoffContract) -> RiskTier:
    """
    Deterministic risk tier classification.
    Evaluated in priority order — first matching condition wins.
    CLAUDE.md §13.
    """
    # Already forced by policy gate
    if contract.required_tier_from_policy == "FULL_CONTRACT":
        return RiskTier.FULL_CONTRACT

    # Force FULL_CONTRACT conditions (any single one is sufficient):
    # 1. PERMANENT reversibility
    if contract.reversibility == ReversibilityClass.PERMANENT:
        return RiskTier.FULL_CONTRACT

    # 2. Cascade rows above threshold
    total_cascade = sum(c.estimated_rows for c in contract.cascade)
    if total_cascade > _CASCADE_ROW_THRESHOLD:
        return RiskTier.FULL_CONTRACT

    # 3. External trigger volume above threshold
    if len(contract.external_triggers) >= _EXTERNAL_TRIGGER_THRESHOLD:
        return RiskTier.FULL_CONTRACT

    # 4. High risk score
    if contract.risk_score > _HIGH_RISK_SCORE:
        return RiskTier.FULL_CONTRACT

    # 5. Historical rejection surfaced
    if any(h.outcome == "REJECTED" for h in contract.historical_precedents):
        return RiskTier.FULL_CONTRACT

    # 6. Prompt injection risk present
    if contract.prompt_injection_risk:
        return RiskTier.FULL_CONTRACT

    # AUTO_EXECUTE: read operations passing all checks
    if (
        contract.operation_type == OperationType.SELECT
        and not contract.policy_violations
        and contract.risk_score < 0.3
        and not contract.required_tier_from_policy
    ):
        return RiskTier.AUTO

    # STANDARD_REVIEW: reversible writes within thresholds
    return RiskTier.STANDARD


def _compute_final_risk_score(contract: HandoffContract) -> float:
    """
    Compute final risk score using full blast radius data.
    Range: 0.0 (trivially safe) to 1.0 (maximum risk).
    """
    score = contract.risk_score  # carry forward preliminary score

    # Add cascade risk
    total_cascade = sum(c.estimated_rows for c in contract.cascade)
    if total_cascade > 500_000:
        score = min(1.0, score + 0.25)
    elif total_cascade > 100_000:
        score = min(1.0, score + 0.15)
    elif total_cascade > 10_000:
        score = min(1.0, score + 0.10)

    # Historical rejections increase score
    rejection_count = sum(1 for h in contract.historical_precedents if h.outcome == "REJECTED")
    score = min(1.0, score + rejection_count * 0.05)

    # External triggers add risk
    score = min(1.0, score + len(contract.external_triggers) * 0.05)

    # Low row confidence adds uncertainty
    if contract.row_confidence < 0.5:
        score = min(1.0, score + 0.05)

    return round(score, 3)


def _derive_rollback_plan(contract: HandoffContract) -> str | None:
    """
    Mechanically derive rollback procedure from classification.
    No LLM — rollback is derivable or it does not exist (CLAUDE.md §22, invariant 3).
    """
    rev = contract.reversibility
    op = contract.operation_type
    table = contract.primary_table
    cond = contract.condition

    if rev == ReversibilityClass.PERMANENT:
        return None  # No automated rollback for PERMANENT operations

    if rev == ReversibilityClass.REVERSIBLE_AUTOMATED:
        if op == OperationType.DELETE and table:
            if cond:
                return (
                    f"Restore: rows were deleted from {table} WHERE {cond}. "
                    "Recovery requires restore from backup or PITR to pre-deletion state."
                )
        if op == OperationType.INSERT and table:
            return f"DELETE FROM {table} WHERE [same condition as INSERT — verify before executing]"
        if op == OperationType.UPDATE and table and cond:
            return (
                f"Restore original values from a pre-execution snapshot or backup. "
                f"Table: {table}, original filter: WHERE {cond}."
            )
        return "Automated recovery: restore from pre-execution snapshot if available."

    if rev == ReversibilityClass.REVERSIBLE_PITR:
        return (
            "Point-in-time recovery required. "
            "Contact DBA to restore database to state before this operation's timestamp."
        )

    if rev == ReversibilityClass.PARTIAL:
        return (
            "Partial recovery possible. Database changes may be reversible via PITR; "
            "external side effects (API calls, webhooks) cannot be automatically reversed."
        )

    return None


def _assemble_prose_summary(contract: HandoffContract) -> str:
    """
    Assemble a plain-language summary of the consequence contract.
    In Phase 5 this will be LLM-generated from the same structured fields.
    """
    parts = []

    # Intent
    parts.append(f"Operation: {contract.intent_summary or contract.raw_sql[:120]}")

    # Primary impact
    parts.append(
        f"Primary impact: {contract.primary_table or 'unknown'} — "
        f"~{contract.estimated_primary_rows:,} rows affected"
        + (f" (confidence: {contract.row_confidence:.0%})" if contract.row_confidence else "")
    )

    # Cascade
    if contract.cascade:
        cascade_desc = ", ".join(
            f"{c.table} (~{c.estimated_rows:,} rows, {c.cascade_action})"
            for c in contract.cascade[:3]
        )
        if len(contract.cascade) > 3:
            cascade_desc += f" and {len(contract.cascade) - 3} more"
        parts.append(f"Cascade: {cascade_desc}")

    # External triggers
    if contract.external_triggers:
        trigger_desc = ", ".join(t.trigger_name for t in contract.external_triggers)
        parts.append(f"External actions (cannot be undone): {trigger_desc}")

    # Reversibility
    rev = contract.reversibility
    if rev:
        parts.append(f"Reversibility: {rev.value} — {contract.reversibility_reason}")

    # Risk tier
    if contract.risk_tier:
        parts.append(f"Risk tier: {contract.risk_tier.value}")

    # Policy violations
    if contract.policy_violations:
        violation_names = ", ".join(v.rule_name for v in contract.policy_violations)
        parts.append(f"Policy flags: {violation_names}")

    # Historical precedents
    if contract.historical_precedents:
        top = contract.historical_precedents[0]
        parts.append(
            f"Historical: top precedent {top.operation_id} "
            f"({top.outcome}) — {top.decision_reason[:80]}"
        )

    return ". ".join(parts) + "."
