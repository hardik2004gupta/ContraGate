"""
ContraGate Orchestrator — FastAPI HTTP service, port 8001.

The orchestrator manages LangGraph workflow execution and exposes HTTP
endpoints for the proxy to start and query workflows.

In Phase 2 / local dev: the proxy imports orchestrator code directly for
simplicity. In production (Railway), these are separate services.

Endpoints:
  POST /v1/workflows          — Start a new workflow for a tool-call manifest
  GET  /v1/workflows/{id}     — Query workflow status
  GET  /health                — Health check
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from orchestrator.graph import run_workflow
from orchestrator.workflow_store import workflow_store, WorkflowStatus

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)

PORT = int(os.environ.get("ORCHESTRATOR_PORT", "8001"))


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ContraGate orchestrator starting on port %d", PORT)
    yield
    logger.info("ContraGate orchestrator shutting down")


app = FastAPI(
    title="ContraGate Orchestrator",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "contragate-orchestrator",
        "version": "0.2.0",
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
    }


class StartWorkflowRequest(BaseModel):
    manifest: dict
    approval_id: str | None = None


@app.post("/v1/workflows")
async def start_workflow(body: StartWorkflowRequest) -> dict:
    """
    Start the full LangGraph workflow for a tool-call manifest.
    Returns the operation_id for polling.

    In practice, the proxy calls this after intercepting a tool call.
    The workflow runs asynchronously — this endpoint returns immediately
    with the operation_id.
    """
    import asyncio
    from orchestrator.states.intake import run_intake

    manifest = body.manifest
    if body.approval_id:
        manifest["_override_operation_id"] = body.approval_id

    # Build initial contract and store record
    initial_contract = run_intake(manifest)
    record = await workflow_store.create(initial_contract, manifest)

    # Run workflow in background
    async def _run():
        try:
            await workflow_store.update_status(record.approval_id, WorkflowStatus.RUNNING)
            final_contract = await run_workflow(manifest)
            await workflow_store.update_contract(record.approval_id, final_contract)
            from orchestrator.handoff_schema import ApprovalState
            state_map = {
                ApprovalState.APPROVED: WorkflowStatus.COMPLETED,
                ApprovalState.REJECTED: WorkflowStatus.REJECTED,
                ApprovalState.AUTO_REJECTED: WorkflowStatus.AUTO_REJECTED,
                ApprovalState.TIMED_OUT: WorkflowStatus.TIMED_OUT,
                ApprovalState.MODIFIED: WorkflowStatus.RUNNING,
            }
            status = state_map.get(final_contract.approval_state, WorkflowStatus.COMPLETED)
            await workflow_store.update_status(record.approval_id, status)
        except Exception as exc:
            logger.error("Workflow failed for %s: %s", record.approval_id, exc)
            await workflow_store.update_status(record.approval_id, WorkflowStatus.FAILED)

    asyncio.create_task(_run())
    return {"operation_id": record.approval_id, "status": "RUNNING"}


@app.get("/v1/workflows/{operation_id}")
async def get_workflow_status(operation_id: str) -> dict:
    record = await workflow_store.get(operation_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"Workflow {operation_id!r} not found")
    return record.to_status_response()


if __name__ == "__main__":
    uvicorn.run(
        "orchestrator.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
    )
