"""
Async approval protocol — PENDING_HUMAN_APPROVAL response, polling, SSE.

Protocol (CLAUDE.md §16):
When human review is required, the proxy returns IMMEDIATELY:
{
  "status": "PENDING_HUMAN_APPROVAL",
  "approval_id": "cg_7f3a9b12",
  "message": "Action paused for ContraGate consequence review.",
  "poll_url": "/v1/approvals/cg_7f3a9b12/status",
  "sse_url": "/v1/approvals/cg_7f3a9b12/stream",
  "estimated_review_seconds": 300
}

Non-negotiable invariants enforced here:
  - No duplicate approval: approval_id decided exactly once
  - No approval replay: workflow_store.record_decision() returns False on re-use
  - No duplicate execution: execution_completed flag checked before any write
  - Decisions route back to the orchestrator via the workflow_store callback

Endpoints:
  GET /v1/approvals/{id}/status   → current status JSON
  GET /v1/approvals/{id}/stream   → SSE stream of status updates
  POST /v1/decisions              → callback from notifier with decision
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from orchestrator.workflow_store import WorkflowStatus, workflow_store

logger = logging.getLogger(__name__)

PROXY_BASE_URL = os.environ.get("PROXY_BASE_URL", "http://localhost:8000")
REVIEW_TIMEOUT_SECONDS = int(os.environ.get("CONTRAGATE_TIMEOUT_SECONDS", "1800"))

router = APIRouter(prefix="/v1")


def build_pending_response(approval_id: str) -> dict[str, Any]:
    """
    Construct the standard PENDING_HUMAN_APPROVAL response.
    Returned immediately to the calling agent — never blocks.
    """
    return {
        "status": "PENDING_HUMAN_APPROVAL",
        "approval_id": approval_id,
        "message": "Action paused for ContraGate consequence review.",
        "poll_url": f"/v1/approvals/{approval_id}/status",
        "sse_url": f"/v1/approvals/{approval_id}/stream",
        "estimated_review_seconds": 300,
        "contragate_version": "0.2.0",
    }


@router.get("/approvals")
async def list_pending_approvals() -> JSONResponse:
    """List all approvals currently awaiting human review (for UI approval queue)."""
    records = await workflow_store.list_pending()
    items = []
    for r in records:
        c = r.contract
        age_seconds = (datetime.now(timezone.utc) - r.created_at).total_seconds()
        items.append({
            "approval_id": r.approval_id,
            "operation_id": r.operation_id,
            "status": r.status.value,
            "risk_tier": c.risk_tier.value if c.risk_tier else "UNKNOWN",
            "primary_table": c.primary_table or "",
            "estimated_rows": c.estimated_primary_rows,
            "reversibility": c.reversibility.value if c.reversibility else "UNKNOWN",
            "intent_summary": c.intent_summary or c.raw_sql[:120],
            "operation_type": c.operation_type.value if c.operation_type else "UNKNOWN",
            "age_seconds": int(age_seconds),
            "timeout_at_seconds": REVIEW_TIMEOUT_SECONDS,
            "seconds_remaining": max(0, int(REVIEW_TIMEOUT_SECONDS - age_seconds)),
            "historical_rejection_count": sum(
                1 for h in c.historical_precedents if h.outcome in ("REJECTED", "ROLLED_BACK")
            ),
            "prompt_injection_risk": c.prompt_injection_risk,
            "created_at": r.created_at.isoformat() + "Z",
        })
    return JSONResponse(content={"approvals": items})


@router.get("/approvals/{approval_id}/contract")
async def get_full_contract(approval_id: str) -> JSONResponse:
    """Get the full HandoffContract for the contract view UI."""
    record = await workflow_store.get(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id!r} not found")
    import json as _json
    return JSONResponse(content={
        "status": record.status.value,
        "approval_id": record.approval_id,
        "contract": _json.loads(record.contract.model_dump_json()),
    })


@router.get("/audit")
async def list_audit_records(limit: int = 100) -> JSONResponse:
    """List all workflow records for the audit log UI, newest first."""
    records = await workflow_store.list_all(limit=limit)
    items = []
    for r in records:
        c = r.contract
        from orchestrator.states.audit import _determine_outcome
        try:
            outcome = _determine_outcome(c)
        except Exception:
            outcome = r.status.value
        items.append({
            "operation_id": r.operation_id,
            "status": r.status.value,
            "outcome": outcome,
            "risk_tier": c.risk_tier.value if c.risk_tier else "UNKNOWN",
            "primary_table": c.primary_table or "",
            "operation_type": c.operation_type.value if c.operation_type else "UNKNOWN",
            "estimated_rows": c.estimated_primary_rows,
            "actual_rows": c.actual_primary_rows,
            "blast_radius_delta": c.blast_radius_accuracy_delta,
            "intent_summary": c.intent_summary or c.raw_sql[:120],
            "decision": c.human_decision,
            "decision_reason": c.decision_reason,
            "reversibility": c.reversibility.value if c.reversibility else None,
            "execution_success": c.execution_success,
            "created_at": r.created_at.isoformat() + "Z",
            "updated_at": r.updated_at.isoformat() + "Z",
        })
    return JSONResponse(content={"records": items})


@router.get("/approvals/{approval_id}/status")
async def get_approval_status(approval_id: str) -> JSONResponse:
    """
    Polling endpoint — calling agent polls this until status is terminal.
    Returns current workflow status and execution result if available.
    """
    record = await workflow_store.get(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id!r} not found")

    return JSONResponse(content=record.to_status_response())


@router.get("/approvals/{approval_id}/stream")
async def stream_approval_status(approval_id: str) -> StreamingResponse:
    """
    SSE endpoint — real-time status updates pushed to the calling agent.

    The client receives a Server-Sent Events stream. Each update is a JSON
    object matching the polling response format. The stream terminates when
    the workflow reaches a terminal state.
    """
    record = await workflow_store.get(approval_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Approval {approval_id!r} not found")

    async def _event_generator():
        terminal = {
            WorkflowStatus.APPROVED,
            WorkflowStatus.REJECTED,
            WorkflowStatus.AUTO_REJECTED,
            WorkflowStatus.AUTO_EXECUTED,
            WorkflowStatus.COMPLETED,
            WorkflowStatus.FAILED,
            WorkflowStatus.TIMED_OUT,
        }

        queue = await workflow_store.subscribe(approval_id)
        try:
            # Send current state immediately
            current = await workflow_store.get(approval_id)
            if current:
                payload = json.dumps(current.to_status_response())
                yield f"data: {payload}\n\n"
                if current.status in terminal:
                    return

            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=30.0)
                    payload = json.dumps(event)
                    yield f"data: {payload}\n\n"

                    status = WorkflowStatus(event.get("status", "RUNNING"))
                    if status in terminal:
                        break
                except asyncio.TimeoutError:
                    # Heartbeat to keep connection alive
                    yield ": heartbeat\n\n"
        finally:
            await workflow_store.unsubscribe(approval_id, queue)

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


class DecisionPayload(BaseModel):
    approval_id: str
    decision: str
    reason: str
    approver_id: str = "notifier"
    modification_constraints: str | None = None


@router.post("/decisions")
async def record_decision(body: DecisionPayload) -> JSONResponse:
    """
    Callback endpoint — called by the notifier when a human decides.
    Records the decision in the workflow store (which the orchestrator polls).

    Security: An approval_id can be decided exactly once (no replay).
    """
    if len(body.reason) < 10:
        raise HTTPException(
            status_code=400,
            detail="Decision reason must be at least 10 characters",
        )
    if body.decision not in ("APPROVE", "REJECT", "MODIFY", "REQUEST_PREREQUISITE"):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid decision: {body.decision!r}",
        )

    recorded = await workflow_store.record_decision(
        approval_id=body.approval_id,
        decision=body.decision,
        reason=body.reason,
        approver_id=body.approver_id,
        modification_constraints=body.modification_constraints,
    )

    if not recorded:
        raise HTTPException(
            status_code=409,
            detail=(
                f"Approval {body.approval_id!r} not found or already decided (no replay allowed)"
            ),
        )

    # Update workflow_store status to match decision
    status_map = {
        "APPROVE": WorkflowStatus.APPROVED,
        "REJECT": WorkflowStatus.REJECTED,
        "MODIFY": WorkflowStatus.RUNNING,
        "REQUEST_PREREQUISITE": WorkflowStatus.REJECTED,
    }
    await workflow_store.update_status(body.approval_id, status_map[body.decision])

    return JSONResponse(content={"recorded": True, "approval_id": body.approval_id})
