"""
Typed HandoffContract schema — Pydantic v2.

The HandoffContract is the single structured JSON object flowing through the entire
pipeline. Every field is typed. Agents write to their designated fields only and
append to workflow_provenance on every write. Inter-agent prose communication is
prohibited. Schema validation failure raises a hard error — the pipeline does not
proceed.

Security invariants (CLAUDE.md §22):
- No LLM-produced reversibility classification (invariant 2)
- No LLM-generated rollback SQL (invariant 3)
- Every important action must have provenance (invariant 14)
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class OperationType(str, Enum):
    DDL = "DDL"
    DELETE = "DELETE"
    UPDATE = "UPDATE"
    INSERT = "INSERT"
    SELECT = "SELECT"
    UNKNOWN = "UNKNOWN"


class ReversibilityClass(str, Enum):
    REVERSIBLE_AUTOMATED = "REVERSIBLE_AUTOMATED"
    REVERSIBLE_PITR = "REVERSIBLE_PITR"
    PARTIAL = "PARTIAL"
    PERMANENT = "PERMANENT"


class RiskTier(str, Enum):
    AUTO = "AUTO"
    STANDARD = "STANDARD"
    FULL_CONTRACT = "FULL_CONTRACT"


class ApprovalState(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    MODIFIED = "MODIFIED"
    AUTO_REJECTED = "AUTO_REJECTED"
    TIMED_OUT = "TIMED_OUT"


class SourceType(str, Enum):
    INTERNAL_SYSTEM = "internal_system"
    EXTERNAL_USER_INPUT = "external_user_input"
    EXTERNAL_AGENT = "external_agent"


class CascadeEntry(BaseModel):
    table: str
    estimated_rows: int
    actual_rows: int | None = None
    cascade_action: str
    depth: int


class ExternalTrigger(BaseModel):
    trigger_name: str
    event: str
    extension: str
    estimated_calls: int | None = None
    target_endpoint: str | None = None
    sandbox_log_entry: str | None = None


class HistoricalOperation(BaseModel):
    operation_id: str
    intent_summary: str
    tables: list[str]
    outcome: str
    decision_reason: str
    similarity_score: float
    jaccard_score: float
    rerank_score: float


class PolicyViolation(BaseModel):
    rule_id: str
    rule_name: str
    severity: str
    description: str


class ProvenanceEntry(BaseModel):
    agent: str
    field_written: str
    timestamp: datetime
    llm_involved: bool


class HandoffContract(BaseModel):
    # Identity
    operation_id: str
    tenant_id: str
    submitted_by: str
    source_type: SourceType
    submission_timestamp: datetime = Field(default_factory=datetime.utcnow)

    # Raw input (always wrapped in untrusted_input tags before LLM processing)
    raw_sql: str
    raw_intent: str | None = None

    # Analyzer Agent outputs
    intent_summary: str = ""
    operation_type: OperationType = OperationType.UNKNOWN
    primary_table: str = ""
    condition: str = ""
    estimated_primary_rows: int = 0
    row_confidence: float = 0.0
    cascade: list[CascadeEntry] = Field(default_factory=list)
    external_triggers: list[ExternalTrigger] = Field(default_factory=list)
    reversibility: ReversibilityClass | None = None
    reversibility_reason: str = ""
    automated_recovery_sql: str | None = None
    permanent_components: list[str] = Field(default_factory=list)
    prompt_injection_risk: bool = False

    # Context and Simulation Agent outputs
    retrieval_available: bool = True
    historical_precedents: list[HistoricalOperation] = Field(default_factory=list)
    simulation_available: bool = True
    simulation_executed: bool = False
    actual_primary_rows: int | None = None
    actual_cascade: list[CascadeEntry] = Field(default_factory=list)
    sandbox_trigger_log: list[dict[str, Any]] = Field(default_factory=list)
    simulation_timeout: bool = False
    sequence_gap_warning: bool = False

    # Contract Agent outputs
    risk_tier: RiskTier | None = None
    risk_score: float = 0.0
    intent_summary_prose: str = ""
    rollback_plan: str | None = None
    contract_assembled: bool = False
    approval_contract_json: str = ""  # Serialized ApprovalContract (Phase 5+)

    # Policy Gate outputs
    policy_violations: list[PolicyViolation] = Field(default_factory=list)
    auto_reject_triggered: bool = False
    auto_reject_reason: str | None = None
    required_tier_from_policy: str | None = None

    # Human Review
    approval_state: ApprovalState = ApprovalState.PENDING
    human_decision: str | None = None
    decision_reason: str | None = None
    decision_timestamp: datetime | None = None
    approver_id: str | None = None
    modification_constraints: str | None = None

    # Execution
    execution_success: bool | None = None
    execution_timestamp: datetime | None = None
    actual_rows_post_execution: int | None = None
    blast_radius_accuracy_delta: float | None = None

    # Provenance
    workflow_provenance: list[ProvenanceEntry] = Field(default_factory=list)
    reanalysis_count: int = 0
    stale_fields: list[str] = Field(default_factory=list)

    def add_provenance(
        self,
        agent: str,
        field_written: str,
        llm_involved: bool = False,
    ) -> None:
        self.workflow_provenance.append(
            ProvenanceEntry(
                agent=agent,
                field_written=field_written,
                timestamp=datetime.utcnow(),
                llm_involved=llm_involved,
            )
        )

    @model_validator(mode="after")
    def validate_decision_reason_length(self) -> "HandoffContract":
        if self.human_decision in ("APPROVE", "REJECT") and self.decision_reason:
            if len(self.decision_reason) < 10:
                raise ValueError(
                    "decision_reason must be at least 10 characters for APPROVE/REJECT decisions"
                )
        return self

    class Config:
        use_enum_values = False
