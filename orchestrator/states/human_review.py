"""
HUMAN_REVIEW state — send contract to notifier, await human decision with 30-min timeout.

Decision options (CLAUDE.md §17):
  APPROVE           — reason ≥ 10 chars; PERMANENT ops also need acknowledgement
  REJECT            — reason ≥ 10 chars; logged to audit and memory
  MODIFY            — triggers selective re-analysis (stale states only)
  REQUEST_PREREQ    — opens prerequisite specification form

Timeout behavior:
  No decision within 30 minutes → approval_state = TIMED_OUT → routed to AUDIT
  as AUTO_REJECT with reason "Human review timed out — auto-rejected".

Security: polling checks both workflow_store and contract.approval_state.
No approval is recorded without passing through record_decision, which
enforces the minimum-reason-length requirement.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone

from orchestrator import mcp_client
from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    RiskTier,
)

logger = logging.getLogger(__name__)

REVIEW_TIMEOUT_SECONDS = float(
    os.environ.get("CONTRAGATE_TIMEOUT_SECONDS", "1800")
)
UI_BASE_URL = os.environ.get("UI_BASE_URL", "http://localhost:3000")
PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "http://contragate-proxy:8000")
POLL_INTERVAL_SECONDS = 5.0


async def run_human_review(contract: HandoffContract) -> HandoffContract:
    """
    Notify the approver and wait for a human decision.
    Returns contract with approval_state set by the human (or TIMED_OUT).
    """
    start = datetime.now(timezone.utc)
    op_id = contract.operation_id

    # ── Send approval request to notifier ───────────────────────────────────
    historical_rejections = sum(
        1 for h in contract.historical_precedents if h.outcome == "REJECTED"
    )
    total_rows = contract.estimated_primary_rows + sum(
        c.estimated_rows for c in contract.cascade
    )

    try:
        await mcp_client.notifier(
            "send_approval_request",
            {
                "approval_id": op_id,
                "operation_summary": contract.intent_summary or contract.raw_sql[:200],
                "risk_tier": contract.risk_tier.value if contract.risk_tier else "UNKNOWN",
                "primary_table": contract.primary_table or "unknown",
                "estimated_rows": total_rows,
                "reversibility": (
                    contract.reversibility.value if contract.reversibility else "UNKNOWN"
                ),
                "historical_rejection_count": historical_rejections,
                "ui_url": f"{UI_BASE_URL}/review/{op_id}",
                "poll_url": f"{PROXY_BASE_URL}/v1/approvals/{op_id}/status",
            },
        )
    except mcp_client.MCPCallError as exc:
        logger.warning(
            "Failed to send approval request to notifier: %s — continuing anyway", exc
        )

    contract.add_provenance(
        agent="HUMAN_REVIEW",
        field_written="approval_notification_sent",
        llm_involved=False,
    )

    # ── Persist the fully-analyzed contract and set status ───────────────────
    # By this point all three agents have written their results to `contract`.
    # Persist now so the UI can display the populated contract while we wait,
    # and set the workflow status to PENDING_HUMAN_APPROVAL so list_pending()
    # returns this record in the approval queue.
    from orchestrator.workflow_store import WorkflowStatus, workflow_store

    await workflow_store.update_contract(op_id, contract)
    await workflow_store.update_status(op_id, WorkflowStatus.PENDING_HUMAN_APPROVAL)

    # ── Poll for human decision ──────────────────────────────────────────────
    # The workflow store is updated by the proxy when the notifier posts a callback.
    # We poll it here at POLL_INTERVAL_SECONDS until a decision arrives or timeout.

    # Reset approval_state to PENDING when re-entering HUMAN_REVIEW after MODIFY.
    # Without this the poll loop immediately finds approval_state=MODIFIED, breaks,
    # and routes back to analysis — creating an infinite loop. Persisting the reset
    # also allows record_decision() to accept the new approval decision.
    if contract.approval_state == ApprovalState.MODIFIED:
        contract.approval_state = ApprovalState.PENDING
        await workflow_store.update_contract(op_id, contract)
        await workflow_store.update_status(op_id, WorkflowStatus.PENDING_HUMAN_APPROVAL)

    deadline = start.timestamp() + REVIEW_TIMEOUT_SECONDS

    while datetime.now(timezone.utc).timestamp() < deadline:
        await asyncio.sleep(POLL_INTERVAL_SECONDS)

        record = await workflow_store.get(op_id)
        if record is None:
            logger.error("Workflow record not found for %s — auto-rejecting", op_id)
            break

        if record.contract.approval_state != ApprovalState.PENDING:
            # Copy decision fields from the stored contract
            contract.approval_state = record.contract.approval_state
            contract.human_decision = record.contract.human_decision
            contract.decision_reason = record.contract.decision_reason
            contract.decision_timestamp = record.contract.decision_timestamp
            contract.approver_id = record.contract.approver_id
            contract.modification_constraints = record.contract.modification_constraints
            break

    else:
        # Timeout path
        logger.warning(
            "Human review timed out after %.0f seconds — auto-rejecting %s",
            REVIEW_TIMEOUT_SECONDS, op_id,
        )
        contract.approval_state = ApprovalState.TIMED_OUT
        contract.decision_reason = "Human review timed out — auto-rejected by ContraGate."
        contract.decision_timestamp = datetime.now(timezone.utc)

    elapsed_min = (datetime.now(timezone.utc) - start).total_seconds() / 60.0
    contract.add_provenance(
        agent="HUMAN_REVIEW",
        field_written="approval_state,human_decision,decision_reason,decision_timestamp",
        llm_involved=False,
    )

    logger.info(
        "HUMAN_REVIEW complete",
        extra={
            "operation_id": op_id,
            "decision": contract.approval_state.value,
            "elapsed_min": round(elapsed_min, 2),
        },
    )
    return contract
