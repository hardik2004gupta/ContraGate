"""
LangGraph state machine definition — ContraGate orchestrator.

Eight states, guard conditions, and routing logic as specified in CLAUDE.md §10.

Graph structure:
  INTAKE → RISK_GATE ─── auto_reject ──→ AUDIT
                     ├── auto_execute ─→ AUDIT
                     └── full_pipeline → ANALYSIS
                                              ↓
                                       CONTEXT_AND_SIM
                                              ↓
                                          CONTRACT
                                              ↓
                                       HUMAN_REVIEW ── timed_out ──→ AUDIT
                                              ├── approved ──────────→ EXECUTION → AUDIT
                                              ├── rejected ──────────→ AUDIT
                                              └── modified ──────────→ ANALYSIS (selective)

All state transitions are logged as provenance entries in the HandoffContract.
Guard conditions are in orchestrator.guards — purely deterministic, no LLM.
"""

from __future__ import annotations

import logging
from typing import Any

from langgraph.graph import END, StateGraph

from orchestrator.guards import route_from_human_review, route_from_risk_gate
from orchestrator.handoff_schema import ApprovalState, HandoffContract
from orchestrator.states.analysis import run_analysis
from orchestrator.states.audit import run_audit
from orchestrator.states.context_sim import run_context_sim
from orchestrator.states.contract import run_contract
from orchestrator.states.execution import run_execution
from orchestrator.states.human_review import run_human_review
from orchestrator.states.intake import run_intake
from orchestrator.states.risk_gate import run_risk_gate

logger = logging.getLogger(__name__)

# ── LangGraph state type ──────────────────────────────────────────────────────
# LangGraph state is a dict keyed by field name. We carry the HandoffContract
# as a single field and return it mutated from every node.

State = dict[str, Any]


def _contract(state: State) -> HandoffContract:
    """Extract HandoffContract from LangGraph state dict."""
    return state["contract"]


def _put(contract: HandoffContract) -> State:
    """Wrap HandoffContract back into LangGraph state dict."""
    return {"contract": contract}


# ── Node implementations ──────────────────────────────────────────────────────

async def node_intake(state: State) -> State:
    """INTAKE node — builds initial HandoffContract from the manifest."""
    manifest = state.get("manifest", {})
    contract = run_intake(manifest)
    return _put(contract)


async def node_risk_gate(state: State) -> State:
    contract = await run_risk_gate(_contract(state))
    return _put(contract)


async def node_analysis(state: State) -> State:
    contract = await run_analysis(_contract(state))
    return _put(contract)


async def node_context_sim(state: State) -> State:
    contract = await run_context_sim(_contract(state))
    return _put(contract)


async def node_contract(state: State) -> State:
    contract = await run_contract(_contract(state))
    return _put(contract)


async def node_human_review(state: State) -> State:
    contract = await run_human_review(_contract(state))
    return _put(contract)


async def node_execution(state: State) -> State:
    contract = await run_execution(_contract(state))
    return _put(contract)


async def node_audit(state: State) -> State:
    contract = await run_audit(_contract(state))
    return _put(contract)


# ── Conditional routing functions ─────────────────────────────────────────────

def route_risk_gate(state: State) -> str:
    """
    Route from RISK_GATE:
      auto_reject   → AUDIT  (hard policy violation)
      auto_execute  → AUDIT  (fast-path SELECT)
      full_pipeline → ANALYSIS
    """
    return route_from_risk_gate(_contract(state))


def route_human_review(state: State) -> str:
    """
    Route from HUMAN_REVIEW:
      approved  → EXECUTION
      rejected  → AUDIT
      timed_out → AUDIT
      modified  → ANALYSIS  (selective re-analysis)
    """
    return route_from_human_review(_contract(state))


# ── Graph construction ─────────────────────────────────────────────────────────

def build_graph() -> StateGraph:
    """
    Build and compile the ContraGate LangGraph workflow.
    Returns a compiled graph ready to invoke.
    """
    graph = StateGraph(dict)

    # Register nodes
    graph.add_node("intake", node_intake)
    graph.add_node("risk_gate", node_risk_gate)
    graph.add_node("analysis", node_analysis)
    graph.add_node("context_sim", node_context_sim)
    graph.add_node("contract", node_contract)
    graph.add_node("human_review", node_human_review)
    graph.add_node("execution", node_execution)
    graph.add_node("audit", node_audit)

    # Entry point
    graph.set_entry_point("intake")

    # Linear edges
    graph.add_edge("intake", "risk_gate")

    # Conditional edge from RISK_GATE
    graph.add_conditional_edges(
        "risk_gate",
        route_risk_gate,
        {
            "auto_reject": "audit",
            "auto_execute": "audit",
            "full_pipeline": "analysis",
        },
    )

    # Linear edges through agent pipeline
    graph.add_edge("analysis", "context_sim")
    graph.add_edge("context_sim", "contract")
    graph.add_edge("contract", "human_review")

    # Conditional edge from HUMAN_REVIEW
    graph.add_conditional_edges(
        "human_review",
        route_human_review,
        {
            "approved": "execution",
            "rejected": "audit",
            "timed_out": "audit",
            "modified": "analysis",  # selective re-analysis — stale_fields controls which sub-steps run
        },
    )

    # Execution → Audit
    graph.add_edge("execution", "audit")

    # Audit → END
    graph.add_edge("audit", END)

    return graph.compile()


# Module-level compiled graph instance — imported by proxy and tests
_compiled_graph = None


def get_compiled_graph() -> Any:
    """Lazy-initialize the compiled graph singleton."""
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


async def run_workflow(manifest: dict[str, Any]) -> HandoffContract:
    """
    Run the complete ContraGate workflow for a tool-call manifest.

    Returns the final HandoffContract after the AUDIT state completes.
    This is the primary entry point used by the proxy.
    """
    graph = get_compiled_graph()
    initial_state = {"manifest": manifest}

    final_state: State = await graph.ainvoke(initial_state)
    contract: HandoffContract = final_state["contract"]

    logger.info(
        "Workflow complete",
        extra={
            "operation_id": contract.operation_id,
            "approval_state": contract.approval_state.value,
            "risk_tier": contract.risk_tier.value if contract.risk_tier else None,
        },
    )
    return contract
