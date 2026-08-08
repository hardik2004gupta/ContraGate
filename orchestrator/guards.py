"""
Guard condition implementations for LangGraph state transitions.

All guards are pure deterministic functions — no LLM calls, no database access.
They take HandoffContract and return str routing keys or bool.

Security invariant: Risk tier classification is NEVER left to LLM discretion.
These guards determine state routing based on the typed HandoffContract fields
written by deterministic infrastructure code.
"""

from __future__ import annotations

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    ReversibilityClass,
    RiskTier,
)


def route_from_risk_gate(contract: HandoffContract) -> str:
    """
    Route from RISK_GATE to:
      - 'auto_reject' if a hard policy rule triggered
      - 'auto_execute' if the operation is low-risk (fast path)
      - 'full_pipeline' otherwise
    """
    if contract.auto_reject_triggered:
        return "auto_reject"
    if _is_fast_path(contract):
        return "auto_execute"
    return "full_pipeline"


def _is_fast_path(contract: HandoffContract) -> bool:
    """
    Fast path conditions (ALL must be true):
      1. Operation type is SELECT
      2. EXPLAIN cost below threshold (50,000)
      3. No PII table involvement (no triggered policies)
      4. No hard or soft policy violations
      5. Risk score below 0.3
    """
    from orchestrator.handoff_schema import OperationType
    if contract.operation_type != OperationType.SELECT:
        return False
    if contract.policy_violations:
        return False
    if contract.risk_score >= 0.3:
        return False
    if contract.required_tier_from_policy:
        return False
    return True


def route_from_human_review(contract: HandoffContract) -> str:
    """
    Route from HUMAN_REVIEW to:
      - 'timed_out' if 30-minute timeout elapsed (AUTO-REJECT)
      - 'approved'  if human approved
      - 'rejected'  if human rejected
      - 'modified'  if human chose Modify with Constraints
      - 'rejected'  as safe fallback for any unexpected state
    """
    if contract.approval_state == ApprovalState.TIMED_OUT:
        return "timed_out"
    if contract.approval_state == ApprovalState.APPROVED:
        return "approved"
    if contract.approval_state == ApprovalState.REJECTED:
        return "rejected"
    if contract.approval_state == ApprovalState.MODIFIED:
        return "modified"
    return "rejected"


def requires_full_contract_by_reversibility(contract: HandoffContract) -> bool:
    """
    Return True if the operation's reversibility forces FULL_CONTRACT tier.
    PERMANENT reversibility → FULL_CONTRACT regardless of risk score.
    """
    return contract.reversibility == ReversibilityClass.PERMANENT


def requires_full_contract_by_history(contract: HandoffContract) -> bool:
    """
    Return True if historical retrieval surfaced any REJECTED operation.
    Historical rejection → escalates to FULL_CONTRACT.
    """
    return any(
        h.outcome == "REJECTED" for h in contract.historical_precedents
    )


def human_approved(contract: HandoffContract) -> bool:
    return contract.approval_state == ApprovalState.APPROVED


def human_rejected(contract: HandoffContract) -> bool:
    return contract.approval_state in (
        ApprovalState.REJECTED, ApprovalState.AUTO_REJECTED, ApprovalState.TIMED_OUT
    )


def human_modified(contract: HandoffContract) -> bool:
    return contract.approval_state == ApprovalState.MODIFIED


def timed_out(contract: HandoffContract) -> bool:
    return contract.approval_state == ApprovalState.TIMED_OUT


def should_auto_execute(contract: HandoffContract) -> bool:
    return contract.risk_tier == RiskTier.AUTO and not contract.auto_reject_triggered


def should_auto_reject(contract: HandoffContract) -> bool:
    return contract.auto_reject_triggered


def needs_selective_reanalysis(contract: HandoffContract) -> bool:
    """
    Return True if this is a re-analysis pass after MODIFY.
    On re-analysis: only stale fields are re-run; fresh fields are preserved.
    """
    return contract.reanalysis_count > 0 and bool(contract.stale_fields)


def sandbox_retry_needed(contract: HandoffContract, attempt: int) -> bool:
    """Return True if sandbox timed out and we have retries remaining (max 2)."""
    return contract.simulation_timeout and attempt < 2
