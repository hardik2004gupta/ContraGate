"""
Contract section assembly — agents/contract/contract_builder.py

Pure deterministic assembler. Takes validated structured state from the
HandoffContract and constructs the ApprovalContract data model.

Responsibilities (this module ONLY):
  - Build all four contract sections from HandoffContract fields
  - Derive rollback plan mechanically from reversibility classification
  - Set requires_permanent_acknowledgement based on reversibility
  - Populate cascade impact, external actions, historical precedents, system flags
  - Accept LLM-generated prose strings and attach them without modifying factual fields

This module does NOT:
  - Call the LLM
  - Query databases
  - Perform policy decisions
  - Calculate row counts
  - Perform retrieval

Security (CLAUDE.md §22, invariants 2, 3, 12):
  - Invariant 2: risk_tier originates from risk_rules.py — this module does not recompute it
  - Invariant 3: rollback_plan is mechanically derived — no LLM call
  - Invariant 12: ApprovalContract validated by Pydantic on construction
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone, timedelta

from orchestrator.handoff_schema import (
    HandoffContract,
    OperationType,
    ReversibilityClass,
    RiskTier,
)
from agents.contract.contract_schema import (
    ApprovalContract,
    CascadeImpact,
    ContractSections,
    ExternalActionEntry,
    HasThisHappenedBefore,
    HistoricalPrecedentEntry,
    PolicyViolationEntry,
    SystemFlags,
    WhatCannotBeUndone,
    WhatWillHappen,
)
from agents.contract.risk_rules import LOW_CONFIDENCE_THRESHOLD

# CLAUDE.md §10 — 30-minute human review timeout
_REVIEW_TIMEOUT_SECONDS = 1800


def build_approval_contract(
    contract: HandoffContract,
    risk_tier: RiskTier,
    risk_score: float,
    rollback_plan: str | None,
    operation_summary: str,
    database_changes_explanation: str,
    external_effects_explanation: str,
    historical_summary: str,
) -> ApprovalContract:
    """
    Assemble the complete ApprovalContract from deterministic fields and
    LLM-generated prose.

    The prose strings (operation_summary, etc.) are attached directly — this
    function does NOT allow the LLM to change any numerical or classification
    fields. Risk tier and reversibility originate from the deterministic pipeline.
    """
    timeout_at = (
        datetime.now(timezone.utc) + timedelta(seconds=_REVIEW_TIMEOUT_SECONDS)
    ).isoformat()
    assembled_at = datetime.now(timezone.utc).isoformat()

    sections = ContractSections(
        what_will_happen=_build_section1(contract, operation_summary),
        what_cannot_be_undone=_build_section2(
            contract,
            rollback_plan,
            database_changes_explanation,
            external_effects_explanation,
        ),
        has_this_happened_before=_build_section3(contract, historical_summary),
        system_flags=_build_section4(contract),
    )

    return ApprovalContract(
        approval_id=f"approval_{contract.operation_id}_{uuid.uuid4().hex[:8]}",
        operation_id=contract.operation_id,
        tenant_id=contract.tenant_id,
        risk_tier=risk_tier.value,
        risk_score=risk_score,
        risk_tier_deterministic=True,
        requires_permanent_acknowledgement=_requires_permanent_acknowledgement(contract),
        sections=sections,
        timeout_at=timeout_at,
        assembled_at=assembled_at,
    )


def derive_rollback_plan(contract: HandoffContract) -> str | None:
    """
    Mechanically derive rollback procedure from reversibility classification.

    Returns None for PERMANENT operations — there is no automated recovery.
    No LLM — rollback is derivable from schema metadata or it does not exist
    (CLAUDE.md §22, invariant 3).
    """
    rev = contract.reversibility
    table = contract.primary_table or "the affected table"
    cond = contract.condition

    if rev is None:
        return None

    if rev == ReversibilityClass.PERMANENT:
        return None  # No automated rollback for PERMANENT operations

    if rev == ReversibilityClass.REVERSIBLE_AUTOMATED:
        if contract.operation_type == OperationType.INSERT and table:
            return (
                f"DELETE FROM {table} WHERE [match the INSERT's identifying condition]. "
                "Verify carefully before executing — ensure the DELETE targets exactly "
                "the rows that were inserted."
            )
        if contract.operation_type in (OperationType.DELETE, OperationType.UPDATE):
            cond_note = f"original filter: {cond}" if cond else "no condition recorded"
            return (
                f"Restore rows to {table}: requires restore from a pre-execution snapshot "
                f"or backup ({cond_note}). "
                "Contact DBA to perform point-in-time restore to before this operation's timestamp."
            )
        return "Automated recovery: restore from pre-execution snapshot if available."

    if rev == ReversibilityClass.REVERSIBLE_PITR:
        return (
            "Point-in-time recovery required. "
            "Contact your DBA to restore the database to a state before this operation's "
            "timestamp. Recovery time depends on database size and PITR infrastructure."
        )

    if rev == ReversibilityClass.PARTIAL:
        return (
            "Partial recovery possible. Database changes may be recoverable via PITR; "
            "external side effects (API calls, webhooks that already fired) cannot be "
            "automatically reversed."
        )

    return None


# ── Private section builders ──────────────────────────────────────────────────

def _requires_permanent_acknowledgement(contract: HandoffContract) -> bool:
    """PERMANENT operations require the human to check an explicit acknowledgement."""
    return contract.reversibility == ReversibilityClass.PERMANENT


def _build_section1(
    contract: HandoffContract,
    operation_summary: str,
) -> WhatWillHappen:
    """Section 1: What will happen?"""
    cascade_impact = [
        CascadeImpact(
            table=c.table,
            estimated_rows=c.estimated_rows,
            actual_rows=c.actual_rows,
            cascade_action=c.cascade_action,
            depth=c.depth,
        )
        for c in contract.cascade
    ]

    external_actions = [
        ExternalActionEntry(
            trigger_name=t.trigger_name,
            event=t.event,
            extension=t.extension,
            estimated_calls=t.estimated_calls,
            target_endpoint=t.target_endpoint,
            sandbox_logged=bool(t.sandbox_log_entry),
            note="Cannot be simulated — would fire in production",
        )
        for t in contract.external_triggers
    ]

    total_estimated = contract.estimated_primary_rows + sum(
        c.estimated_rows for c in contract.cascade
    )

    total_actual: int | None = None
    if contract.actual_primary_rows is not None:
        cascade_actual = sum(
            c.actual_rows
            for c in contract.actual_cascade
            if c.actual_rows is not None
        )
        total_actual = contract.actual_primary_rows + cascade_actual

    sim_note: str | None = None
    if contract.simulation_executed and contract.actual_primary_rows is not None:
        sim_note = (
            f"Staging simulation affected {contract.actual_primary_rows:,} rows "
            f"(estimated: {contract.estimated_primary_rows:,})"
        )
    elif not contract.simulation_available:
        sim_note = "Staging simulation was unavailable — estimates only"

    return WhatWillHappen(
        operation_summary=operation_summary,
        operation_type=contract.operation_type.value,
        primary_table=contract.primary_table or "(unknown)",
        source_condition=contract.condition or "(none)",
        estimated_primary_rows=contract.estimated_primary_rows,
        actual_primary_rows=contract.actual_primary_rows,
        simulation_row_delta_note=sim_note,
        cascade_impact=cascade_impact,
        total_estimated_rows=total_estimated,
        total_actual_rows=total_actual,
        external_actions=external_actions,
    )


def _build_section2(
    contract: HandoffContract,
    rollback_plan: str | None,
    database_changes_explanation: str,
    external_effects_explanation: str,
) -> WhatCannotBeUndone:
    """Section 2: What cannot be undone?"""
    reversibility = (
        contract.reversibility.value if contract.reversibility else "UNKNOWN"
    )
    return WhatCannotBeUndone(
        reversibility=reversibility,
        reversibility_reason=contract.reversibility_reason,
        database_changes_explanation=database_changes_explanation,
        external_effects_explanation=external_effects_explanation,
        permanent_components=list(contract.permanent_components),
        rollback_plan=rollback_plan,
        requires_permanent_acknowledgement=_requires_permanent_acknowledgement(contract),
    )


def _build_section3(
    contract: HandoffContract,
    historical_summary: str,
) -> HasThisHappenedBefore:
    """Section 3: Has this happened before?"""
    precedents = []
    for i, h in enumerate(contract.historical_precedents[:3]):
        precedents.append(
            HistoricalPrecedentEntry(
                operation_id=h.operation_id,
                intent_summary=h.intent_summary,
                tables=list(h.tables),
                outcome=h.outcome,            # Preserved exactly — not modified
                decision_reason=h.decision_reason,
                similarity_score=h.similarity_score,
                jaccard_score=h.jaccard_score,
                rerank_score=h.rerank_score,
                is_top_result=(i == 0),
            )
        )

    contains_rejected = any(p.outcome == "REJECTED" for p in precedents)

    return HasThisHappenedBefore(
        retrieval_available=contract.retrieval_available,
        precedents=precedents,
        historical_summary=historical_summary,
        contains_rejected_outcome=contains_rejected,
    )


def _build_section4(contract: HandoffContract) -> SystemFlags:
    """Section 4: What does the system flag?"""
    policy_violations = [
        PolicyViolationEntry(
            rule_id=v.rule_id,
            rule_name=v.rule_name,
            severity=v.severity,
            description=v.description,
        )
        for v in contract.policy_violations
    ]

    historical_rejection_warning = any(
        h.outcome == "REJECTED" for h in contract.historical_precedents
    )

    has_unsimulable_external = bool(contract.external_triggers)

    confidence_reduced = contract.row_confidence < 0.7
    confidence_reason: str | None = None
    if contract.row_confidence == 0.0:
        confidence_reason = "Row statistics not available for this table"
    elif contract.row_confidence < LOW_CONFIDENCE_THRESHOLD:
        confidence_reason = (
            f"Very low confidence ({contract.row_confidence:.0%}) — "
            "statistics may be severely stale or missing"
        )
    elif contract.row_confidence < 0.5:
        confidence_reason = (
            f"Low confidence ({contract.row_confidence:.0%}) — "
            "statistics may be stale"
        )
    elif confidence_reduced:
        confidence_reason = (
            f"Moderate confidence ({contract.row_confidence:.0%}) — "
            "statistics may be slightly stale"
        )

    return SystemFlags(
        policy_violations=policy_violations,
        historical_rejection_warning=historical_rejection_warning,
        simulation_unavailable=not contract.simulation_available,
        retrieval_unavailable=not contract.retrieval_available,
        prompt_injection_risk=contract.prompt_injection_risk,
        external_actions_cannot_be_simulated=has_unsimulable_external,
        confidence_reduced=confidence_reduced,
        confidence_reason=confidence_reason,
        sequence_gap_warning=contract.sequence_gap_warning,
    )
