"""
ContraGate Agents HTTP Service — port 8002.

Exposes the three ContraGate agents as HTTP endpoints for:
  - /health  — confirms all three agents load and are ready
  - /v1/agents/analyze       — invoke AnalyzerAgent on a HandoffContract
  - /v1/agents/context-sim   — invoke ContextSimAgent on a HandoffContract
  - /v1/agents/contract      — invoke ContractAgent on a HandoffContract

In the local Docker stack and Railway combined service, the orchestrator imports
agents directly as Python modules (no HTTP hop). This service exists to:
  1. Prove the agent code loads and runs in its own container
  2. Enable future horizontal scaling of the agent tier independently
  3. Satisfy Railway's separate agents service requirement (railway.json)

Security invariants preserved:
  - Agents never make direct DB connections — all access via mcp_client
  - Risk classification is ALWAYS deterministic (rule engine, not LLM)
  - HandoffContract is validated (Pydantic v2) on ingestion and egestion
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

from orchestrator.handoff_schema import HandoffContract

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"agents","msg":"%(message)s"}',
)

PORT = int(os.environ.get("AGENTS_PORT", "8002"))

# ── Import all three agents at module load time ───────────────────────────────
# This is the critical gate: if any agent fails to import (missing dependency,
# broken module), the container's health check will fail.
try:
    from agents.analyzer.agent import AnalyzerAgent
    _analyzer_ready = True
except Exception as _e:
    logger.error("AnalyzerAgent import failed: %s", _e)
    _analyzer_ready = False

try:
    from agents.context_sim.agent import ContextSimAgent
    _context_sim_ready = True
except Exception as _e:
    logger.error("ContextSimAgent import failed: %s", _e)
    _context_sim_ready = False

try:
    from agents.contract.agent import ContractAgent
    _contract_ready = True
except Exception as _e:
    logger.error("ContractAgent import failed: %s", _e)
    _contract_ready = False

_all_ready = _analyzer_ready and _context_sim_ready and _contract_ready


@asynccontextmanager
async def lifespan(app: FastAPI):
    if _all_ready:
        logger.info("All 3 agents loaded successfully — agents service ready on port %d", PORT)
    else:
        logger.error(
            "One or more agents failed to load — "
            "analyzer=%s context_sim=%s contract=%s",
            _analyzer_ready, _context_sim_ready, _contract_ready,
        )
    yield
    logger.info("Agents service shutting down")


app = FastAPI(
    title="ContraGate Agents Service",
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
    """
    Health check — returns 200 only when all three agents have loaded successfully.
    A 503 here means the container started but agent imports failed.
    """
    if not _all_ready:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "analyzer": _analyzer_ready,
                "context_sim": _context_sim_ready,
                "contract": _contract_ready,
            },
        )
    return {
        "status": "ok",
        "service": "contragate-agents",
        "version": "0.2.0",
        "agents": {
            "analyzer": _analyzer_ready,
            "context_sim": _context_sim_ready,
            "contract": _contract_ready,
        },
        "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
    }


class AgentRequest(BaseModel):
    contract: dict  # HandoffContract serialized as dict


class AgentResponse(BaseModel):
    contract: dict  # Updated HandoffContract


@app.post("/v1/agents/analyze")
async def run_analyze(body: AgentRequest) -> AgentResponse:
    """
    Run the Analyzer Agent on the provided HandoffContract.

    The orchestrator calls this when running as separate services.
    In the default Docker Compose configuration, agents are imported
    directly by the orchestrator (no HTTP hop needed).
    """
    if not _analyzer_ready:
        raise HTTPException(503, detail="AnalyzerAgent is not ready")
    contract = HandoffContract.model_validate(body.contract)
    analyzer = AnalyzerAgent(contract.operation_id, contract.tenant_id)
    updated = await analyzer.analyze(contract)
    return AgentResponse(contract=updated.model_dump(mode="json"))


@app.post("/v1/agents/context-sim")
async def run_context_sim(body: AgentRequest) -> AgentResponse:
    """
    Run the Context + Simulation Agent on the provided HandoffContract.
    """
    if not _context_sim_ready:
        raise HTTPException(503, detail="ContextSimAgent is not ready")
    contract = HandoffContract.model_validate(body.contract)
    agent = ContextSimAgent(contract.operation_id, contract.tenant_id)
    updated = await agent.run(contract)
    return AgentResponse(contract=updated.model_dump(mode="json"))


@app.post("/v1/agents/contract")
async def run_contract(body: AgentRequest) -> AgentResponse:
    """
    Run the Contract Agent on the provided HandoffContract.
    Applies deterministic risk rules and LLM prose summarization.
    """
    if not _contract_ready:
        raise HTTPException(503, detail="ContractAgent is not ready")
    contract = HandoffContract.model_validate(body.contract)
    agent = ContractAgent(contract.operation_id, contract.tenant_id)
    updated = await agent.run(contract)
    return AgentResponse(contract=updated.model_dump(mode="json"))


if __name__ == "__main__":
    uvicorn.run(
        "agents.main:app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
    )
