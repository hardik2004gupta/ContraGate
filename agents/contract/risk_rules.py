"""
Deterministic risk tier classification — agents/contract/risk_rules.py

Pure function — no LLM, no network, no database.
Identical inputs MUST produce identical output (IMPLEMENTATION_PLAN.md Phase 5).

Risk tiers (CLAUDE.md §13):
  AUTO         — SELECT, low cost, no PII, no policy violations, risk_score < 0.3
  STANDARD     — reversible write, risk_score 0.3–0.7, no historical rejection
  FULL_CONTRACT — any of the FULL_CONTRACT conditions (evaluated in priority order)

FULL_CONTRACT conditions (priority order — first match wins):
  1. required_tier_from_policy == "FULL_CONTRACT"  (hard policy override)
  2. reversibility == PERMANENT
  3. historical precedent with outcome == "REJECTED"
  4. prompt_injection_risk == True
  5. cascade rows > CASCADE_ROW_THRESHOLD (50,000)
  6. external trigger count >= EXTERNAL_TRIGGER_THRESHOLD (5)
  7. risk_score > HIGH_RISK_SCORE (0.7)
  8. row_confidence < LOW_CONFIDENCE_THRESHOLD (uncertainty_score > 0.75)

Security: CLAUDE.md §22, invariants 2 and 5.
"""

from __future__ import annotations

from orchestrator.handoff_schema import (
    HandoffContract,
    OperationType,
    ReversibilityClass,
    RiskTier,
)

# Thresholds — CLAUDE.md §13
CASCADE_ROW_THRESHOLD: int = 50_000
EXTERNAL_TRIGGER_THRESHOLD: int = 5
HIGH_RISK_SCORE: float = 0.7
# uncertainty_score > 0.75 → row_confidence < 0.25
LOW_CONFIDENCE_THRESHOLD: float = 0.25

# Base risk scores by operation type (deterministic starting point)
_BASE_SCORES: dict[str, float] = {
    OperationType.SELECT.value: 0.05,
    OperationType.INSERT.value: 0.15,
    OperationType.UPDATE.value: 0.25,
    OperationType.DELETE.value: 0.35,
    OperationType.DDL.value: 0.50,
    OperationType.UNKNOWN.value: 0.20,
}


def classify_risk(contract: HandoffContract) -> tuple[RiskTier, float]:
    """
    Classify the risk tier and compute the final risk score.

    Returns (risk_tier, risk_score).
    Pure function — no side effects, no I/O, no LLM.
    Invariant: classify_risk(c) == classify_risk(c) for any contract c.
    """
    risk_score = _compute_risk_score(contract)
    risk_tier = _apply_tier_rules(contract, risk_score)
    return risk_tier, risk_score


def _compute_risk_score(contract: HandoffContract) -> float:
    """
    Compute a deterministic risk score in [0.0, 1.0].

    Components (additive, clamped to 1.0):
      - Base score from operation type
      - Primary row count magnitude (log-scaled buckets)
      - Cascade row total (log-scaled buckets)
      - External trigger count (per-trigger increment)
      - Historical rejection count (per-rejection increment)
      - Row confidence (low confidence → higher uncertainty → higher risk)
      - Policy violation count (per-violation increment)
      - PARTIAL reversibility (moderate risk addition)
    """
    op_value = (
        contract.operation_type.value
        if contract.operation_type
        else OperationType.UNKNOWN.value
    )
    score: float = _BASE_SCORES.get(op_value, 0.20)

    # Primary rows contribution (log-scaled buckets)
    primary_rows = contract.estimated_primary_rows or 0
    if primary_rows > 1_000_000:
        score = min(1.0, score + 0.30)
    elif primary_rows > 100_000:
        score = min(1.0, score + 0.20)
    elif primary_rows > 10_000:
        score = min(1.0, score + 0.15)
    elif primary_rows > 1_000:
        score = min(1.0, score + 0.08)

    # Cascade rows contribution (log-scaled buckets)
    cascade_total = sum(c.estimated_rows for c in contract.cascade)
    if cascade_total > 500_000:
        score = min(1.0, score + 0.25)
    elif cascade_total > 100_000:
        score = min(1.0, score + 0.15)
    elif cascade_total > 10_000:
        score = min(1.0, score + 0.10)
    elif cascade_total > 0:
        score = min(1.0, score + 0.05)

    # External triggers (each adds 0.05)
    trigger_count = len(contract.external_triggers)
    score = min(1.0, score + trigger_count * 0.05)

    # Historical rejections (each adds 0.05)
    rejection_count = sum(
        1 for h in contract.historical_precedents if h.outcome == "REJECTED"
    )
    score = min(1.0, score + rejection_count * 0.05)

    # Row confidence — low confidence raises score
    if contract.row_confidence < LOW_CONFIDENCE_THRESHOLD:
        score = min(1.0, score + 0.10)
    elif contract.row_confidence < 0.5:
        score = min(1.0, score + 0.05)

    # Policy violations (each adds 0.10)
    violation_count = len(contract.policy_violations)
    score = min(1.0, score + violation_count * 0.10)

    # PARTIAL reversibility adds moderate risk
    if contract.reversibility == ReversibilityClass.PARTIAL:
        score = min(1.0, score + 0.10)

    return round(score, 3)


def _apply_tier_rules(contract: HandoffContract, risk_score: float) -> RiskTier:
    """
    Apply FULL_CONTRACT conditions in explicit priority order.

    Priority is explicit — not implicit via max() or sorting.
    First matching condition wins (CLAUDE.md §13).
    """
    # ── 1. Hard policy override (always wins) ──────────────────────────────────
    if contract.required_tier_from_policy == "FULL_CONTRACT":
        return RiskTier.FULL_CONTRACT

    # ── 2. PERMANENT reversibility ─────────────────────────────────────────────
    if contract.reversibility == ReversibilityClass.PERMANENT:
        return RiskTier.FULL_CONTRACT

    # ── 3. Historical rejection surfaced ──────────────────────────────────────
    if any(h.outcome == "REJECTED" for h in contract.historical_precedents):
        return RiskTier.FULL_CONTRACT

    # ── 4. Prompt injection risk ───────────────────────────────────────────────
    if contract.prompt_injection_risk:
        return RiskTier.FULL_CONTRACT

    # ── 5. Cascade rows above threshold (strictly greater than) ───────────────
    cascade_total = sum(c.estimated_rows for c in contract.cascade)
    if cascade_total > CASCADE_ROW_THRESHOLD:
        return RiskTier.FULL_CONTRACT

    # ── 6. External trigger volume at or above threshold ──────────────────────
    if len(contract.external_triggers) >= EXTERNAL_TRIGGER_THRESHOLD:
        return RiskTier.FULL_CONTRACT

    # ── 7. High risk score ─────────────────────────────────────────────────────
    if risk_score > HIGH_RISK_SCORE:
        return RiskTier.FULL_CONTRACT

    # ── 8. High uncertainty (low confidence) ──────────────────────────────────
    if contract.row_confidence < LOW_CONFIDENCE_THRESHOLD:
        return RiskTier.FULL_CONTRACT

    # ── AUTO_EXECUTE: safe read operations ────────────────────────────────────
    # The Risk Gate has already routed obvious trivial SELECTs to auto_execute.
    # The Contract Agent may still classify a SELECT as AUTO if it passes all checks.
    if (
        contract.operation_type == OperationType.SELECT
        and not contract.policy_violations
        and risk_score < 0.3
        and not contract.required_tier_from_policy
    ):
        return RiskTier.AUTO

    # ── STANDARD_REVIEW: reversible write operations within thresholds ─────────
    return RiskTier.STANDARD
