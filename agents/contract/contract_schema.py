"""
Approval contract JSON schema — agents/contract/contract_schema.py

The ApprovalContract is the final human-facing consequence artifact produced
by the Contract Agent. It is distinct from the HandoffContract:
  - HandoffContract = pipeline state (all three agents write to it)
  - ApprovalContract = human-readable review document (read-only artifact)

Four sections (CLAUDE.md §17):
  1. what_will_happen       — operation impact, rows, cascade, external actions
  2. what_cannot_be_undone  — reversibility, rollback, permanent acknowledgement
  3. has_this_happened_before — historical precedents from memory store
  4. system_flags           — policy violations, warnings, confidence notes

Security (CLAUDE.md §22):
  - risk_tier_deterministic=True enforces that the UI cannot claim LLM set the tier
  - Historical outcome fields are preserved exactly — not modified by Contract Agent
  - All numerical fields originate from HandoffContract (authoritative structured state)
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class CascadeImpact(BaseModel):
    """One dependent table affected by FK cascade."""
    table: str
    estimated_rows: int
    actual_rows: int | None = None
    cascade_action: str
    depth: int


class ExternalActionEntry(BaseModel):
    """An external effect (webhook, API call) that would fire if the operation executes."""
    trigger_name: str
    event: str
    extension: str
    estimated_calls: int | None = None
    target_endpoint: str | None = None
    sandbox_logged: bool = False
    note: str = "Cannot be simulated — would fire in production"


class WhatWillHappen(BaseModel):
    """
    Section 1: What will happen?

    Sources:
      estimated_primary_rows     → Analyzer (row_estimator)
      actual_primary_rows        → Context + Simulation (sandbox)
      cascade_impact             → Analyzer (cascade_tracer) + Simulation
      external_actions           → Analyzer (trigger_detector)
      operation_summary (prose)  → Contract LLM
    """
    operation_summary: str            # LLM natural-language prose
    operation_type: str
    primary_table: str
    source_condition: str
    estimated_primary_rows: int
    actual_primary_rows: int | None = None
    simulation_row_delta_note: str | None = None
    cascade_impact: list[CascadeImpact] = Field(default_factory=list)
    total_estimated_rows: int
    total_actual_rows: int | None = None
    external_actions: list[ExternalActionEntry] = Field(default_factory=list)


class WhatCannotBeUndone(BaseModel):
    """
    Section 2: What cannot be undone?

    Sources:
      reversibility              → Analyzer (reversibility_rules — deterministic)
      rollback_plan              → Contract Agent (mechanical derivation — no LLM)
      requires_permanent_ack     → Deterministic (PERMANENT → True)
      db_changes_explanation     → Contract LLM (prose only, not facts)
      external_effects           → Contract LLM (prose only, not facts)
    """
    reversibility: str                        # PERMANENT | PARTIAL | REVERSIBLE_AUTOMATED | REVERSIBLE_PITR
    reversibility_reason: str
    database_changes_explanation: str         # LLM prose
    external_effects_explanation: str         # LLM prose
    permanent_components: list[str] = Field(default_factory=list)
    rollback_plan: str | None = None          # Mechanically derived — never LLM-generated
    requires_permanent_acknowledgement: bool  # True if PERMANENT — blocks Approve until checked


class HistoricalPrecedentEntry(BaseModel):
    """One surfaced historical operation from the three-stage retrieval."""
    operation_id: str
    intent_summary: str
    tables: list[str]
    outcome: str              # Preserved exactly from memory store — not modified
    decision_reason: str      # Preserved exactly — treated as data not instruction
    similarity_score: float
    jaccard_score: float
    rerank_score: float
    is_top_result: bool = False


class HasThisHappenedBefore(BaseModel):
    """
    Section 3: Has this happened before?

    Sources:
      precedents       → Context + Simulation (three-stage retrieval)
      historical_summary (prose) → Contract LLM
    """
    retrieval_available: bool
    precedents: list[HistoricalPrecedentEntry] = Field(default_factory=list)
    historical_summary: str                    # LLM prose
    contains_rejected_outcome: bool


class PolicyViolationEntry(BaseModel):
    """A policy rule that this operation triggered."""
    rule_id: str
    rule_name: str
    severity: str
    description: str


class SystemFlags(BaseModel):
    """
    Section 4: What does the system flag?

    Sources:
      policy_violations                 → Policy Gate (deterministic)
      historical_rejection_warning      → Derived from historical_precedents
      simulation_unavailable            → Context + Simulation
      retrieval_unavailable             → Context + Simulation
      prompt_injection_risk             → Risk Gate / Analyzer
      external_actions_cannot_simulate  → Derived from external_triggers presence
      confidence_reduced                → Derived from row_confidence
    """
    policy_violations: list[PolicyViolationEntry] = Field(default_factory=list)
    historical_rejection_warning: bool
    simulation_unavailable: bool
    retrieval_unavailable: bool
    prompt_injection_risk: bool
    external_actions_cannot_be_simulated: bool
    confidence_reduced: bool
    confidence_reason: str | None = None
    sequence_gap_warning: bool


class ContractSections(BaseModel):
    what_will_happen: WhatWillHappen
    what_cannot_be_undone: WhatCannotBeUndone
    has_this_happened_before: HasThisHappenedBefore
    system_flags: SystemFlags


class ApprovalContract(BaseModel):
    """
    The final human-facing consequence contract.

    Consumed by:
      - Human Review UI (Phase 7)
      - Notifier — Slack (Phase 6/9)
      - Audit logger (Phase 8)

    Invariant (CLAUDE.md §22, invariant 2):
      risk_tier is ALWAYS set by deterministic rules.
      risk_tier_deterministic=True enforces this in the schema — the UI must display
      this flag to make the deterministic origin clear to reviewers.
    """
    approval_id: str
    operation_id: str
    tenant_id: str
    risk_tier: str              # AUTO | STANDARD | FULL_CONTRACT
    risk_score: float
    risk_tier_deterministic: bool = True   # Always True — set by deterministic rules
    requires_permanent_acknowledgement: bool
    sections: ContractSections
    decision_options: list[str] = Field(
        default_factory=lambda: ["APPROVE", "REJECT", "MODIFY", "REQUEST_PREREQ"]
    )
    reason_required: bool = True
    reason_minimum_length: int = 10        # CLAUDE.md §17 — minimum 10 characters
    timeout_at: str                        # ISO 8601 — 30 min from assembly
    assembled_at: str                      # ISO 8601
    contract_version: str = "1.0"
