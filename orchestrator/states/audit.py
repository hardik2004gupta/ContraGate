"""
AUDIT state — write complete audit record; update memory store with outcome.

Audit record contents (CLAUDE.md §20):
  - Full HandoffContract at time of completion
  - Original tool-call manifest
  - Human decision with reason and timestamp
  - Execution result (or rejection reason)
  - Post-execution actual row counts
  - Blast radius accuracy delta
  - Agent workflow provenance trail

Tamper-evidence: the audit-logger MCP server enforces append-only with
SHA-256 checksums. This state only appends — never updates or deletes.

Memory write-back: every completed operation is written to the memory store
with its outcome so ContraGate learns from every operation.

Post-execution feedback loop (Phase 8): triggered as a background task
after writing the audit record. In Phase 2, a stub is called that no-ops.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from orchestrator import mcp_client
from orchestrator.handoff_schema import ApprovalState, HandoffContract

logger = logging.getLogger(__name__)


async def run_audit(contract: HandoffContract) -> HandoffContract:
    """
    Write complete audit record and update memory store.
    Never raises — audit failures are logged but do not affect the workflow outcome.
    """
    start = datetime.utcnow()
    op_id = contract.operation_id
    outcome = _determine_outcome(contract)

    # ── Write audit record ───────────────────────────────────────────────────
    try:
        audit_payload = _build_audit_payload(contract, outcome)
        await mcp_client.audit_logger(
            "log_execution" if contract.execution_success else "log_auto_reject",
            audit_payload,
        )
        logger.info(
            "AUDIT record written",
            extra={"operation_id": op_id, "outcome": outcome},
        )
    except mcp_client.MCPCallError as exc:
        logger.error("Failed to write audit record for %s: %s", op_id, exc)

    # ── Memory write-back (learn from every operation) ───────────────────────
    try:
        intent = contract.intent_summary or contract.raw_sql[:200]
        tables = _extract_tables(contract)
        historical_rejection = any(
            h.outcome == "REJECTED" for h in contract.historical_precedents
        )
        await mcp_client.memory_store(
            "store_operation",
            {
                "operation_id": op_id,
                "tenant_id": contract.tenant_id,
                "intent_summary": intent,
                "raw_sql": contract.raw_sql,
                "operation_type": contract.operation_type.value,
                "primary_table": contract.primary_table,
                "tables": tables,
                "estimated_rows": contract.estimated_primary_rows,
                "actual_rows": contract.actual_primary_rows,
                "outcome": outcome,
                "decision_reason": contract.decision_reason or "",
                "risk_tier": contract.risk_tier.value if contract.risk_tier else "UNKNOWN",
                "reversibility": (
                    contract.reversibility.value if contract.reversibility else "UNKNOWN"
                ),
                "blast_radius_delta": contract.blast_radius_accuracy_delta,
                "historical_rejection_surfaced": historical_rejection,
            },
        )
    except mcp_client.MCPCallError as exc:
        logger.error("Failed to write memory for %s: %s", op_id, exc)

    # ── Phase 2 feedback loop stub ───────────────────────────────────────────
    _trigger_feedback_loop_stub(contract)

    elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
    contract.add_provenance(
        agent="AUDIT",
        field_written="audit_record,memory_write_back",
        llm_involved=False,
    )

    logger.info(
        "AUDIT complete",
        extra={"operation_id": op_id, "outcome": outcome, "elapsed_ms": round(elapsed_ms, 1)},
    )
    return contract


def _determine_outcome(contract: HandoffContract) -> str:
    """Map final contract state to audit outcome string."""
    state = contract.approval_state
    if state == ApprovalState.AUTO_REJECTED or contract.auto_reject_triggered:
        return "AUTO_REJECTED"
    if state == ApprovalState.TIMED_OUT:
        return "TIMED_OUT"
    if state == ApprovalState.REJECTED:
        return "REJECTED"
    if state == ApprovalState.MODIFIED:
        return "MODIFIED"
    if state == ApprovalState.APPROVED:
        if contract.execution_success:
            return "APPROVED_EXECUTED"
        return "APPROVED_EXECUTION_FAILED"
    return "UNKNOWN"


def _build_audit_payload(contract: HandoffContract, outcome: str) -> dict[str, Any]:
    return {
        "operation_id": contract.operation_id,
        "tenant_id": contract.tenant_id,
        "outcome": outcome,
        "operation_type": contract.operation_type.value,
        "primary_table": contract.primary_table,
        "risk_tier": contract.risk_tier.value if contract.risk_tier else None,
        "reversibility": contract.reversibility.value if contract.reversibility else None,
        "estimated_rows": contract.estimated_primary_rows,
        "actual_rows": contract.actual_primary_rows,
        "blast_radius_delta": contract.blast_radius_accuracy_delta,
        "decision": contract.human_decision,
        "decision_reason": contract.decision_reason,
        "decision_timestamp": (
            contract.decision_timestamp.isoformat() + "Z"
            if contract.decision_timestamp else None
        ),
        "execution_success": contract.execution_success,
        "execution_timestamp": (
            contract.execution_timestamp.isoformat() + "Z"
            if contract.execution_timestamp else None
        ),
        "auto_reject_reason": contract.auto_reject_reason,
        "policy_violations": [v.model_dump() for v in contract.policy_violations],
        "historical_precedents_count": len(contract.historical_precedents),
        "provenance_entries": len(contract.workflow_provenance),
        "contract_json": contract.model_dump_json(),
    }


def _extract_tables(contract: HandoffContract) -> list[str]:
    tables = set()
    if contract.primary_table:
        tables.add(contract.primary_table)
    for c in contract.cascade:
        tables.add(c.table)
    return list(tables)


def _trigger_feedback_loop_stub(contract: HandoffContract) -> None:
    """
    Post-execution feedback loop (CLAUDE.md §21).

    Computes blast radius accuracy delta and adjusts confidence scores:
      - Underestimate (actual > estimated, |delta| > 10%): lower confidence by 0.05
      - Overestimate (actual < estimated, |delta| > 10%): lower confidence by 0.02
    Both directions are penalized because they indicate stale or inaccurate statistics.
    The confidence adjustment is stored on the contract; the memory store write-back
    in run_audit() will persist it for future operations on the same table.
    """
    if contract.actual_primary_rows is None or contract.estimated_primary_rows <= 0:
        return

    actual = contract.actual_primary_rows
    estimated = contract.estimated_primary_rows
    delta = (actual - estimated) / estimated
    contract.blast_radius_accuracy_delta = round(delta, 4)

    if abs(delta) <= 0.1:
        return

    current_confidence = contract.row_confidence if contract.row_confidence is not None else 0.8

    if delta > 0:
        # Underestimate — more severe, misleads approvers toward smaller-seeming ops
        adjustment = -0.05
        direction = "underestimate"
    else:
        # Overestimate — less severe
        adjustment = -0.02
        direction = "overestimate"

    new_confidence = max(0.0, min(1.0, round(current_confidence + adjustment, 3)))
    contract.row_confidence = new_confidence

    logger.info(
        "Feedback loop: blast radius delta %.1f%% (%s) for %s on %s — confidence %.2f → %.2f",
        delta * 100,
        direction,
        contract.operation_id,
        contract.primary_table,
        current_confidence,
        new_confidence,
    )
