"""
Approval contract JSON schema.

PHASE 0 SCAFFOLD — NOT IMPLEMENTED.
Implementation begins in Phase 5.

The approval contract is the final structured object sent to the UI and the notifier.
It is distinct from the HandoffContract — it is formatted for human consumption.

Structure:
{
  "approval_id": str,
  "operation_id": str,
  "risk_tier": "AUTO_EXECUTE" | "STANDARD_REVIEW" | "FULL_CONTRACT",
  "risk_score": float,
  "requires_permanent_acknowledgement": bool,
  "sections": {
    "what_will_happen": { ... },
    "what_cannot_be_undone": { ... },
    "historical_precedents": { ... },
    "system_flags": { ... }
  },
  "decision_options": ["APPROVE", "REJECT", "MODIFY", "REQUEST_PREREQ"],
  "reason_required": bool,
  "reason_minimum_length": 10,
  "timeout_at": str  // ISO 8601 timestamp
}
"""
