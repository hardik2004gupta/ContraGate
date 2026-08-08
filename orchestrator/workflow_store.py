"""
Workflow store — maps operation_id / approval_id to HandoffContract state.

The proxy reads from this store to serve polling and SSE responses.
The orchestrator writes to it after each state transition.

Persistence: write-through to PostgreSQL when DATABASE_URL is set.
On startup, existing records are loaded from PostgreSQL so PENDING_HUMAN_APPROVAL
approvals survive process restarts. SSE subscriber queues are in-memory only
(transient HTTP connections; clients reconnect after restart).

If DATABASE_URL is not set or the pool fails to connect, the store operates in
in-memory-only mode with a logged warning. This is the graceful degradation path
for local development without Docker.

Thread/task safety: all mutations go through async locks.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from orchestrator.handoff_schema import ApprovalState, HandoffContract

logger = logging.getLogger(__name__)

_DB_URL = os.environ.get("DATABASE_URL")

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS workflow_records (
    operation_id  TEXT PRIMARY KEY,
    status        TEXT        NOT NULL DEFAULT 'RUNNING',
    contract_json JSONB       NOT NULL,
    tool_call_manifest JSONB,
    execution_result   JSONB,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    execution_completed BOOLEAN NOT NULL DEFAULT FALSE
);
CREATE INDEX IF NOT EXISTS idx_wr_status      ON workflow_records(status);
CREATE INDEX IF NOT EXISTS idx_wr_created_at  ON workflow_records(created_at DESC);
"""

_UPSERT_SQL = """
INSERT INTO workflow_records
    (operation_id, status, contract_json, tool_call_manifest,
     execution_result, created_at, updated_at, execution_completed)
VALUES ($1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6, $7, $8)
ON CONFLICT (operation_id) DO UPDATE SET
    status              = EXCLUDED.status,
    contract_json       = EXCLUDED.contract_json,
    tool_call_manifest  = EXCLUDED.tool_call_manifest,
    execution_result    = EXCLUDED.execution_result,
    updated_at          = EXCLUDED.updated_at,
    execution_completed = EXCLUDED.execution_completed
"""


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
        self.created_at: datetime = datetime.now(timezone.utc)
        self.updated_at: datetime = datetime.now(timezone.utc)
        self.execution_completed: bool = False

    def to_status_response(self) -> dict[str, Any]:
        resp: dict[str, Any] = {
            "status": self.status.value,
            "approval_id": self.approval_id,
            "updated_at": self.updated_at.isoformat(),
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
    """
    Write-through workflow store.
    In-memory cache (`_store`) is the fast read path and supports SSE.
    PostgreSQL is the persistence layer that survives restarts.
    """

    def __init__(self) -> None:
        self._store: dict[str, WorkflowRecord] = {}
        self._lock = asyncio.Lock()
        self._subscribers: dict[str, list[asyncio.Queue]] = {}
        self._pool: Any | None = None  # asyncpg.Pool when DB is available

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def initialize(self, database_url: str | None = None) -> None:
        """
        Connect to PostgreSQL and load persisted records into memory.
        Must be called at application startup (e.g., inside FastAPI lifespan).
        Falls back to in-memory-only mode if DB is unavailable.
        """
        url = database_url or _DB_URL
        if not url:
            logger.warning(
                "DATABASE_URL not set — WorkflowStore running in in-memory-only mode. "
                "PENDING_HUMAN_APPROVAL records will not survive restarts."
            )
            return
        try:
            import asyncpg
            self._pool = await asyncpg.create_pool(
                url,
                min_size=2,
                max_size=10,
                command_timeout=10,
            )
            await self._ensure_table()
            await self._load_from_db()
            logger.info("WorkflowStore: PostgreSQL persistence active")
        except Exception as exc:
            logger.warning(
                "WorkflowStore: DB init failed (%s) — falling back to in-memory-only mode", exc
            )
            self._pool = None

    async def close(self) -> None:
        """Close the PostgreSQL connection pool on shutdown."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            logger.info("WorkflowStore: PostgreSQL pool closed")

    async def _ensure_table(self) -> None:
        """Create the workflow_records table if it does not exist."""
        async with self._pool.acquire() as conn:
            await conn.execute(_CREATE_TABLE_SQL)

    async def _load_from_db(self) -> None:
        """Load all persisted records into the in-memory cache on startup."""
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM workflow_records ORDER BY created_at ASC"
            )
        loaded = 0
        for row in rows:
            try:
                contract = HandoffContract.model_validate_json(row["contract_json"])
                record = WorkflowRecord(row["operation_id"], contract)
                record.status = WorkflowStatus(row["status"])
                record.tool_call_manifest = (
                    json.loads(row["tool_call_manifest"])
                    if row["tool_call_manifest"] else None
                )
                record.execution_result = (
                    json.loads(row["execution_result"])
                    if row["execution_result"] else None
                )
                record.created_at = row["created_at"]
                record.updated_at = row["updated_at"]
                record.execution_completed = row["execution_completed"]
                self._store[record.operation_id] = record
                loaded += 1
            except Exception as exc:
                logger.error(
                    "WorkflowStore: failed to load record %s from DB: %s",
                    row["operation_id"], exc,
                )
        logger.info("WorkflowStore: loaded %d record(s) from PostgreSQL", loaded)

    async def _persist(self, record: WorkflowRecord) -> None:
        """Upsert a record to PostgreSQL. No-ops if pool is unavailable."""
        if self._pool is None:
            return
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(
                    _UPSERT_SQL,
                    record.operation_id,
                    record.status.value,
                    record.contract.model_dump_json(),
                    json.dumps(record.tool_call_manifest) if record.tool_call_manifest else None,
                    json.dumps(record.execution_result) if record.execution_result else None,
                    record.created_at,
                    record.updated_at,
                    record.execution_completed,
                )
        except Exception as exc:
            logger.error(
                "WorkflowStore: failed to persist record %s: %s",
                record.operation_id, exc,
            )

    # ── Core operations ───────────────────────────────────────────────────────

    async def create(self, contract: HandoffContract, manifest: dict) -> WorkflowRecord:
        """Create a new workflow record and persist the original tool-call manifest."""
        record = WorkflowRecord(contract.operation_id, contract)
        record.tool_call_manifest = manifest
        async with self._lock:
            self._store[contract.operation_id] = record
        await self._persist(record)
        return record

    async def get(self, approval_id: str) -> WorkflowRecord | None:
        async with self._lock:
            return self._store.get(approval_id)

    async def update_status(self, approval_id: str, status: WorkflowStatus) -> None:
        record = None
        async with self._lock:
            record = self._store.get(approval_id)
            if record:
                record.status = status
                record.updated_at = datetime.now(timezone.utc)
        if record:
            await self._persist(record)
        await self._notify_subscribers(approval_id)

    async def update_contract(self, approval_id: str, contract: HandoffContract) -> None:
        record = None
        async with self._lock:
            record = self._store.get(approval_id)
            if record:
                record.contract = contract
                record.updated_at = datetime.now(timezone.utc)
        if record:
            await self._persist(record)

    async def set_execution_result(self, approval_id: str, result: dict) -> None:
        record = None
        async with self._lock:
            record = self._store.get(approval_id)
            if record:
                record.execution_result = result
                record.execution_completed = True
                record.updated_at = datetime.now(timezone.utc)
        if record:
            await self._persist(record)
        await self._notify_subscribers(approval_id)

    # ── SSE ───────────────────────────────────────────────────────────────────

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

    # ── Query helpers ─────────────────────────────────────────────────────────

    async def list_pending(self) -> list[WorkflowRecord]:
        """Return all records currently awaiting human review, sorted by created_at ascending."""
        async with self._lock:
            return sorted(
                [r for r in self._store.values()
                 if r.status == WorkflowStatus.PENDING_HUMAN_APPROVAL],
                key=lambda r: r.created_at,
            )

    async def list_all(self, limit: int = 200) -> list[WorkflowRecord]:
        """Return all records sorted by created_at descending (newest first)."""
        async with self._lock:
            records = sorted(
                self._store.values(), key=lambda r: r.created_at, reverse=True
            )
            return records[:limit]

    # ── Decision ──────────────────────────────────────────────────────────────

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
        record = None
        async with self._lock:
            record = self._store.get(approval_id)
            if not record:
                return False
            # No approval replay — once decided, cannot be re-decided
            if record.contract.approval_state not in (ApprovalState.PENDING,):
                return False

            from orchestrator.handoff_schema import ApprovalState as AS
            decision_map = {
                "APPROVE": AS.APPROVED,
                "REJECT": AS.REJECTED,
                "MODIFY": AS.MODIFIED,
                "REQUEST_PREREQUISITE": AS.REJECTED,
            }
            record.contract.approval_state = decision_map.get(decision, AS.REJECTED)
            record.contract.human_decision = decision
            record.contract.decision_reason = reason
            record.contract.approver_id = approver_id
            record.contract.decision_timestamp = datetime.now(timezone.utc)
            if modification_constraints:
                record.contract.modification_constraints = modification_constraints
            record.updated_at = datetime.now(timezone.utc)

        if record:
            await self._persist(record)
        await self._notify_subscribers(approval_id)
        return True


# Module-level singleton used by proxy and orchestrator
workflow_store = WorkflowStore()
