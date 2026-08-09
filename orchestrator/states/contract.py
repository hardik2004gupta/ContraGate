"""
CONTRACT state — delegates to real ContractAgent (Phase 5).

Phase 5 behavior: Invokes the ContractAgent which applies:
  1. Deterministic risk rules (no LLM) → risk_tier, risk_score
  2. Mechanical rollback derivation (no LLM) → rollback_plan
  3. Structured LLM natural-language summarization → prose sections
  4. ApprovalContract assembly and Pydantic validation
  5. Serializes ApprovalContract → approval_contract_json

Security invariant (CLAUDE.md §22, invariant 2):
  An LLM must NEVER override a hard policy rule.
  Risk tier classification is entirely deterministic code in agents/contract/risk_rules.py.
  The LLM is used only for prose summarization of already-computed facts.
"""

from __future__ import annotations

import logging

from orchestrator.handoff_schema import HandoffContract

logger = logging.getLogger(__name__)


async def run_contract(contract: HandoffContract) -> HandoffContract:
    """
    Invoke the Contract Agent to produce the final consequence contract.

    Writes to contract: risk_tier, risk_score, rollback_plan,
    intent_summary_prose, approval_contract_json, contract_assembled.
    """
    from agents.contract.agent import ContractAgent

    agent = ContractAgent(contract.operation_id, contract.tenant_id)
    return await agent.run(contract)
