"""
ContraGate MCP Proxy — entry point, port 8000.

Receives incoming JSON-RPC tool calls from external MCP clients.
For every operation:
  1. Intercept and parse the tool call
  2. Quick risk gate check (EXPLAIN + policy)
  3a. Auto-execute path: relay to real MCP target, return result
  3b. Auto-reject path: return rejection immediately
  3c. Review required: return PENDING_HUMAN_APPROVAL, kick off background workflow

Endpoints:
  POST /v1/mcp                        — Main MCP interception endpoint
  GET  /v1/approvals/{id}/status      — Polling endpoint
  GET  /v1/approvals/{id}/stream      — SSE endpoint
  POST /v1/decisions                  — Notifier callback
  GET  /health                        — Health check

Security invariants (CLAUDE.md §22):
  - Original MCP connection NEVER held open during human review
  - EXECUTION state is idempotent
  - Original tool-call manifest persisted keyed by approval_id
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from orchestrator.graph import run_workflow
from orchestrator.workflow_store import WorkflowStatus, workflow_store
from proxy.async_protocol import build_pending_response, router as approval_router
from proxy.interceptor import InterceptionError, intercept
from proxy.webhook_handler import router as webhook_router

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","msg":"%(message)s"}',
)

PORT = int(os.environ.get("PROXY_PORT", "8000"))
TENANT_ID = os.environ.get("CONTRAGATE_TENANT_ID", "demo_tenant")
TARGET_MCP_URL = os.environ.get("TARGET_MCP_URL", "http://contragate-mcp:8010")


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("ContraGate proxy starting on port %d", PORT)
    yield
    logger.info("ContraGate proxy shutting down")


app = FastAPI(
    title="ContraGate MCP Proxy",
    version="0.2.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(approval_router)
app.include_router(webhook_router)


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "service": "contragate-proxy",
        "version": "0.2.0",
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }


@app.post("/v1/mcp")
async def intercept_tool_call(
    request: Request,
    x_contragate_source: str = Header(default="internal_system"),
    x_contragate_caller: str = Header(default="unknown"),
    x_contragate_tenant: str | None = Header(default=None),
) -> JSONResponse:
    """
    Primary MCP interception endpoint.

    The calling agent points its MCP client at this URL instead of the real
    MCP server URL. ContraGate intercepts every tool call here.
    """
    tenant_id = x_contragate_tenant or TENANT_ID

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # ── Parse tool call ───────────────────────────────────────────────────────
    try:
        manifest = intercept(
            body=body,
            caller_id=x_contragate_caller,
            source_type=x_contragate_source,
            tenant_id=tenant_id,
            target_mcp_url=TARGET_MCP_URL,
        )
    except InterceptionError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    manifest_dict = manifest.to_dict()

    # Add manifest hash for identity binding (stale-state guard in EXECUTION)
    _hash_body = {k: v for k, v in manifest_dict.items()}
    manifest_dict["_manifest_hash"] = hashlib.sha256(
        json.dumps(_hash_body, sort_keys=True, default=str).encode()
    ).hexdigest()

    # ── Quick risk gate check ─────────────────────────────────────────────────
    from proxy.risk_gate import quick_check
    import re

    op_type_map = {
        "SELECT": "SELECT",
        "INSERT": "INSERT",
        "UPDATE": "UPDATE",
        "DELETE": "DELETE",
    }
    op_match = re.match(r"^\s*(\w+)", manifest.sql)
    op_type = op_match.group(1).upper() if op_match else "UNKNOWN"
    op_type = op_type_map.get(op_type, "DDL")

    gate_result = await quick_check(
        sql=manifest.sql,
        operation_type=op_type,
        source_type=manifest.source_type,
        tenant_id=tenant_id,
    )

    # ── Auto-reject fast path ─────────────────────────────────────────────────
    if gate_result.routing == "auto_reject":
        logger.info(
            "Auto-rejected by policy gate",
            extra={"reason": gate_result.auto_reject_reason},
        )
        return JSONResponse(
            status_code=200,
            content={
                "status": "AUTO_REJECTED",
                "reason": gate_result.auto_reject_reason or "Policy violation",
                "contragate_version": "0.2.0",
            },
        )

    # ── Auto-execute fast path (SELECT, low cost, no policy violations) ───────
    if gate_result.routing == "auto_execute":
        try:
            from orchestrator import mcp_client
            result = await mcp_client.call(
                TARGET_MCP_URL,
                manifest.tool_name,
                {"sql": manifest.sql, "tenant_id": tenant_id},
            )
            return JSONResponse(
                content={
                    "status": "AUTO_EXECUTED",
                    "result": result,
                    "explain_cost": gate_result.explain_cost,
                    "contragate_version": "0.2.0",
                }
            )
        except Exception as exc:
            logger.error("Auto-execute failed: %s", exc)
            raise HTTPException(status_code=502, detail=f"MCP target error: {exc}")

    # ── Full pipeline — create workflow record and kick off background ─────────
    # We need a preliminary contract to create the workflow record.
    # The orchestrator's INTAKE state creates the real one.
    from orchestrator.states.intake import run_intake

    initial_contract = run_intake(manifest_dict)
    record = await workflow_store.create(initial_contract, manifest_dict)

    # Kick off the full LangGraph workflow in the background.
    # The proxy returns PENDING immediately — never blocks on human review.
    async def _run_pipeline():
        try:
            await workflow_store.update_status(record.approval_id, WorkflowStatus.RUNNING)
            final_contract = await run_workflow(manifest_dict)
            # Sync final contract state to the store
            await workflow_store.update_contract(record.approval_id, final_contract)
            # Map final approval_state to WorkflowStatus
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

    asyncio.create_task(_run_pipeline())

    # Return PENDING immediately — never blocks
    return JSONResponse(
        status_code=202,
        content=build_pending_response(record.approval_id),
    )


@app.post("/v1/mcp/jsonrpc")
async def intercept_jsonrpc(
    request: Request,
    x_contragate_source: str = Header(default="internal_system"),
    x_contragate_caller: str = Header(default="unknown"),
) -> JSONResponse:
    """JSON-RPC 2.0 compatible interception endpoint."""
    body = await request.json()
    request_id = body.get("id")

    response = await intercept_tool_call(
        request=request,
        x_contragate_source=x_contragate_source,
        x_contragate_caller=x_contragate_caller,
    )
    data = json.loads(response.body)

    return JSONResponse(content={
        "jsonrpc": "2.0",
        "id": request_id,
        "result": data,
    })


if __name__ == "__main__":
    uvicorn.run(
        "proxy.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
    )
