"""
Workflow store — maps operation_id / approval_id to HandoffContract state.

The proxy reads from this store to serve polling and SSE responses.
The orchestrator writes to it after each state transition.

In Phase 2 this is an in-memory store. Phase 9 upgrades it to Redis or a
PostgreSQL-backed persistent store for Railway deployment.

Thread/task safety: all mutations go through async locks.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from enum import Enum
from typing import Any

from orchestrator.handoff_schema import ApprovalState, HandoffContract


class WorkflowStatus(str, Enum):
    RUNNING = "RUNNING"
    PENDING_HUMAN_APPROVAL = "PENDING_HUMAN_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AUTO_REJECTED = "AUTO_REJECTED"
    AUTO_EXECUTED = "AUTO_EXECUTED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"


class WorkflowRecord:
    __slots__ = (
        "operation_id", "approval_id", "status", "contract",
        "tool_call_manifest", "execution_result", "created_at",
        "updated_at", "execution_completed",
    )

    def __init__(self, operation_id: str, contract: HandoffContract) -> None:
        self.operation_id = operation_id
        self.approval_id = operation_id  # Same ID used as approval token
        self.status = WorkflowStatus.RUNNING
        self.contract = contract
        self.tool_call_manifest: dict[str, Any] | None = None
        self.execution_result: dict[str, Any] | None = None
        self.created_at = datetime.utcnow()
        self.updated_at = datetime.utcnow()
        self.execution_completed = False

    def to_status_response(self) -> dict[str, Any]:
        resp: dict[str, Any] = {
            "status": self.status.value,
            "approval_id": self.approval_id,
            "updated_at": self.updated_at.isoformat() + "Z",
        }
        if self.status in (
            WorkflowStatus.APPROVED, WorkflowStatus.AUTO_EXECUTED, WorkflowStatus.COMPLETED
        ):
            resp["execution_result"] = self.execution_result
        if self.status in (WorkflowStatus.REJECTED, WorkflowStatus.AUTO_REJECTED):
            resp["reason"] = (
                self.contract.decision_reason or self.contract.auto_reject_reason
            )
        return resp


class WorkflowStore:
    """In-memory store mapping approval_id → WorkflowRecord."""

    def __init__(self) -> None:
        self._store: dict[str, WorkflowRecord] = {}
        self._lock = asyncio.Lock()
        # SSE subscribers: approval_id → list of asyncio.Queue
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    async def create(self, contract: HandoffContract, manifest: dict) -> WorkflowRecord:
        """Create a new workflow record and store the original tool-call manifest."""
        record = WorkflowRecord(contract.operation_id, contract)
        record.tool_call_manifest = manifest
        async with self._lock:
            self._store[contract.operation_id] = record
        return record

    async def get(self, approval_id: str) -> WorkflowRecord | None:
        async with self._lock:
            return self._store.get(approval_id)

    async def update_status(self, approval_id: str, status: WorkflowStatus) -> None:
        async with self._lock:
            record = self._store.get(approval_id)
            if record:
                record.status = status
                record.updated_at = datetime.utcnow()
        await self._notify_subscribers(approval_id)

    async def update_contract(self, approval_id: str, contract: HandoffContract) -> None:
        async with self._lock:
            record = self._store.get(approval_id)
            if record:
                record.contract = contract
                record.updated_at = datetime.utcnow()

    async def set_execution_result(self, approval_id: str, result: dict) -> None:
        async with self._lock:
            record = self._store.get(approval_id)
            if record:
                record.execution_result = result
                record.execution_completed = True
                record.updated_at = datetime.utcnow()
        await self._notify_subscribers(approval_id)

    async def subscribe(self, approval_id: str) -> asyncio.Queue:
        """Register an SSE subscriber for real-time updates."""
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subscribers.setdefault(approval_id, []).append(q)
        return q

    async def unsubscribe(self, approval_id: str, queue: asyncio.Queue) -> None:
        async with self._lock:
            subs = self._subscribers.get(approval_id, [])
            try:
                subs.remove(queue)
            except ValueError:
                pass

    async def _notify_subscribers(self, approval_id: str) -> None:
        async with self._lock:
            record = self._store.get(approval_id)
            subs = list(self._subscribers.get(approval_id, []))
        if record:
            event = record.to_status_response()
            for q in subs:
                await q.put(event)

    async def record_decision(
        self,
        approval_id: str,
        decision: str,
        reason: str,
        approver_id: str = "unknown",
        modification_constraints: str | None = None,
    ) -> bool:
        """
        Record a human decision on the workflow.
        Returns False if the approval_id is not found or already decided.

        Security: An approval_id can be decided exactly once (no replay).
        """
        async with self._lock:
            record = self._store.get(approval_id)
            if not record:
                return False
            # No approval replay — once decided, cannot be re-decided
            if record.contract.approval_state not in (
                ApprovalState.PENDING,
            ):
                return False

            # Apply decision to the contract
            from orchestrator.handoff_schema import ApprovalState as AS
            decision_map = {
                "APPROVE": AS.APPROVED,
                "REJECT": AS.REJECTED,
                "MODIFY": AS.MODIFIED,
            }
            record.contract.approval_state = decision_map.get(decision, AS.REJECTED)
            record.contract.human_decision = decision
            record.contract.decision_reason = reason
            record.contract.approver_id = approver_id
            record.contract.decision_timestamp = datetime.utcnow()
            if modification_constraints:
                record.contract.modification_constraints = modification_constraints
            record.updated_at = datetime.utcnow()

        await self._notify_subscribers(approval_id)
        return True


# Module-level singleton used by proxy and orchestrator
workflow_store = WorkflowStore()
