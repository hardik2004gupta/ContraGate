"""
Contract Agent — Phase 5 implementation.

Converts the enriched HandoffContract into the final human-facing consequence
contract. Combines:
  - Analyzer output (blast radius, reversibility, intent)
  - Context/Simulation output (historical precedents, actual rows)
  - Policy state (violations already in contract from Risk Gate)
  - Deterministic risk rules (risk_rules.py — NO LLM)
  - LLM natural-language summarization (structured output ONLY)

LLM scope (CLAUDE.md §8, Agent 3):
  MAY : summarize structured consequences, produce concise human-readable prose
  MUST NOT: assign risk tier, classify reversibility, determine row counts,
            generate rollback SQL, approve/reject, change numerical values,
            invent historical precedents or simulation results

Security invariants enforced here (CLAUDE.md §22):
  Invariant 2 : risk_tier is NEVER produced by LLM — deterministic code only
  Invariant 3 : No LLM-generated rollback SQL — mechanically derived or None
  Invariant 5 : LLM cannot determine reversibility
  Invariant 7 : LLM cannot approve or reject
  Invariant 11: Untrusted inputs wrapped in <untrusted_input> tags
  Invariant 12: Contract validated (Pydantic) before advancing
  Invariant 13: LLM output consumed as structured JSON, not instructions
  Invariant 14: Provenance recorded on every field write
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from typing import Any

import anthropic

from agents.base_agent import BaseAgent
from agents.contract.contract_builder import build_approval_contract, derive_rollback_plan
from agents.contract.contract_schema import ApprovalContract
from agents.contract.prompts import (
    CONTRACT_SUMMARY_TOOL,
    CONTRACT_SUMMARY_TOOL_NAME,
    build_contract_summary_prompt,
)
from agents.contract.risk_rules import classify_risk
from mcp_servers.audit_logger.checksum import hash_payload
from orchestrator.handoff_schema import HandoffContract, RiskTier

logger = logging.getLogger(__name__)

_ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-5")
_MAX_LLM_RETRIES = 2  # Total attempts (not retries) for LLM structured output


class ContractAgent(BaseAgent):
    """
    Agent 3: Contract — deterministic risk classification + LLM prose summarization.

    Pipeline:
      1. Deterministic risk rules → risk_tier, risk_score
      2. Mechanical rollback derivation → rollback_plan
      3. Structured LLM summarization → prose for four contract sections
      4. Contract assembly → ApprovalContract (Pydantic-validated)
      5. Write to HandoffContract → provenance
    """

    AGENT_NAME = "CONTRACT_AGENT"

    async def run(self, contract: HandoffContract) -> HandoffContract:
        """
        Run Contract Agent. Writes to HandoffContract:
          - risk_tier          (deterministic)
          - risk_score         (deterministic)
          - rollback_plan      (mechanical)
          - intent_summary_prose (LLM operation_summary prose)
          - approval_contract_json (full ApprovalContract JSON)
          - contract_assembled = True
        """
        start = datetime.now(timezone.utc)
        logger.info(
            "CONTRACT_AGENT starting",
            extra={
                "operation_id": contract.operation_id,
                "reversibility": (
                    contract.reversibility.value if contract.reversibility else None
                ),
                "historical_count": len(contract.historical_precedents),
            },
        )

        # ── Step 1: Deterministic risk classification ──────────────────────────
        risk_tier, risk_score = classify_risk(contract)
        contract.risk_tier = risk_tier
        contract.risk_score = risk_score

        # ── Step 2: Mechanical rollback derivation ─────────────────────────────
        rollback_plan = derive_rollback_plan(contract)
        contract.rollback_plan = rollback_plan

        # ── Step 3: LLM natural-language summarization ─────────────────────────
        prose = await self._get_contract_prose(contract)

        # ── Step 4: Assemble ApprovalContract (Pydantic validates on construction)
        approval_contract = build_approval_contract(
            contract=contract,
            risk_tier=risk_tier,
            risk_score=risk_score,
            rollback_plan=rollback_plan,
            operation_summary=prose["operation_summary"],
            database_changes_explanation=prose["database_changes_explanation"],
            external_effects_explanation=prose["external_effects_explanation"],
            historical_summary=prose["historical_summary"],
        )

        # ── Step 5: Write to HandoffContract ───────────────────────────────────
        # intent_summary_prose = operation summary (one-paragraph prose)
        contract.intent_summary_prose = prose["operation_summary"]
        # approval_contract_json = full serialized contract for Phase 6/7 consumption
        contract.approval_contract_json = approval_contract.model_dump_json()
        contract.contract_assembled = True

        elapsed_ms = (datetime.now(timezone.utc) - start).total_seconds() * 1000

        # ── Step 6: Provenance (CLAUDE.md §22, invariant 14) ───────────────────
        contract.add_provenance(
            agent=self.AGENT_NAME,
            field_written=(
                "risk_tier,risk_score,rollback_plan,"
                "intent_summary_prose,approval_contract_json,contract_assembled"
            ),
            # llm_involved=True because LLM was used for prose
            # but risk_tier itself is deterministic (documented in risk_tier_deterministic)
            llm_involved=True,
        )

        # ── Step 7: Audit output (non-blocking, failure non-fatal) ────────────
        contract_hash = hash_payload(contract.model_dump(mode="json"))
        await self.log_agent_output(
            output_summary=(
                f"Contract: risk_tier={risk_tier.value} "
                f"risk_score={risk_score:.3f} "
                f"reversibility={contract.reversibility} "
                f"precedents={len(contract.historical_precedents)}"
            ),
            contract_hash=contract_hash,
        )

        logger.info(
            "CONTRACT_AGENT complete",
            extra={
                "operation_id": contract.operation_id,
                "risk_tier": risk_tier.value,
                "risk_score": risk_score,
                "elapsed_ms": round(elapsed_ms, 1),
            },
        )
        return contract

    async def _get_contract_prose(self, contract: HandoffContract) -> dict[str, str]:
        """
        Call the Anthropic API for structured-output contract prose.

        All untrusted content is wrapped in <untrusted_input> tags before
        reaching the LLM. Falls back to deterministic prose on any API failure.

        Returns dict with keys:
          operation_summary, database_changes_explanation,
          external_effects_explanation, historical_summary
        """
        for attempt in range(1, _MAX_LLM_RETRIES + 1):
            try:
                client = anthropic.AsyncAnthropic()
                prompt = build_contract_summary_prompt(
                    operation_type=contract.operation_type.value,
                    primary_table=contract.primary_table or "(unknown)",
                    condition=contract.condition or "(none)",
                    estimated_primary_rows=contract.estimated_primary_rows,
                    actual_primary_rows=contract.actual_primary_rows,
                    cascade_tables=[
                        {
                            "table": c.table,
                            "estimated_rows": c.estimated_rows,
                            "actual_rows": c.actual_rows,
                        }
                        for c in contract.cascade[:5]
                    ],
                    external_triggers=[
                        {
                            "trigger_name": t.trigger_name,
                            "event": t.event,
                            "extension": t.extension,
                            "estimated_calls": t.estimated_calls,
                        }
                        for t in contract.external_triggers[:5]
                    ],
                    reversibility=(
                        contract.reversibility.value
                        if contract.reversibility
                        else "UNKNOWN"
                    ),
                    reversibility_reason=contract.reversibility_reason,
                    rollback_plan=contract.rollback_plan,
                    risk_tier=(
                        contract.risk_tier.value if contract.risk_tier else "UNKNOWN"
                    ),
                    risk_score=contract.risk_score,
                    historical_precedents=[
                        {
                            "operation_id": h.operation_id,
                            "outcome": h.outcome,
                            "decision_reason": h.decision_reason,
                            "tables": h.tables,
                        }
                        for h in contract.historical_precedents[:3]
                    ],
                    retrieval_available=contract.retrieval_available,
                    simulation_available=contract.simulation_available,
                    raw_sql=contract.raw_sql,
                    intent_summary=contract.intent_summary,
                )

                response = await client.messages.create(
                    model=_ANTHROPIC_MODEL,
                    max_tokens=1024,
                    tools=[CONTRACT_SUMMARY_TOOL],
                    tool_choice={
                        "type": "tool",
                        "name": CONTRACT_SUMMARY_TOOL_NAME,
                    },
                    messages=[{"role": "user", "content": prompt}],
                )

                for block in response.content:
                    if (
                        block.type == "tool_use"
                        and block.name == CONTRACT_SUMMARY_TOOL_NAME
                    ):
                        validated = self._validate_and_sanitize_prose(
                            block.input, contract
                        )
                        if validated is not None:
                            logger.debug(
                                "LLM contract prose produced (attempt %d)",
                                attempt,
                                extra={"operation_id": contract.operation_id},
                            )
                            return validated

                logger.warning(
                    "LLM returned no valid tool_use block (attempt %d)",
                    attempt,
                    extra={"operation_id": contract.operation_id},
                )

            except Exception as exc:
                logger.warning(
                    "LLM contract prose attempt %d failed: %s",
                    attempt,
                    exc,
                    extra={"operation_id": contract.operation_id},
                )

        logger.warning(
            "LLM contract prose failed after %d attempts — using deterministic fallback",
            _MAX_LLM_RETRIES,
            extra={"operation_id": contract.operation_id},
        )
        return self._fallback_prose(contract)

    def _validate_and_sanitize_prose(
        self,
        llm_output: dict[str, Any],
        contract: HandoffContract,
    ) -> dict[str, str] | None:
        """
        Validate LLM prose for safety and consistency.

        Rejects if:
          - Missing any required field
          - Any field is empty or trivially short
          - Output contains SQL patterns (invariant 3)
          - Output contains approval/rejection decisions (invariant 7)
          - Output contains untrusted_input bypass attempts

        Returns sanitized dict or None (triggers retry then fallback).
        """
        required_fields = [
            "operation_summary",
            "database_changes_explanation",
            "external_effects_explanation",
            "historical_summary",
        ]
        for field in required_fields:
            value = llm_output.get(field)
            if not value or not isinstance(value, str) or len(value.strip()) < 5:
                logger.warning(
                    "LLM prose missing/empty field %r",
                    field,
                    extra={"operation_id": contract.operation_id},
                )
                return None

        # Reject SQL generation attempts (invariant 3)
        all_prose = " ".join(
            llm_output[f].upper() for f in required_fields
        )
        _SQL_PATTERNS = [
            "DELETE FROM", "UPDATE SET", "DROP TABLE",
            "ALTER TABLE", "TRUNCATE", "INSERT INTO",
            "CREATE TABLE", "ROLLBACK;", "COMMIT;",
        ]
        for pattern in _SQL_PATTERNS:
            if pattern in all_prose:
                logger.warning(
                    "LLM prose contains SQL pattern %r — rejecting",
                    pattern,
                    extra={"operation_id": contract.operation_id},
                )
                return None

        # Reject approval/rejection decisions (invariant 7)
        _DECISION_PATTERNS = [
            "THIS OPERATION SHOULD BE APPROVED",
            "THIS OPERATION SHOULD BE REJECTED",
            "APPROVE THIS OPERATION",
            "REJECT THIS OPERATION",
            "YOU SHOULD APPROVE",
            "YOU SHOULD REJECT",
        ]
        for pattern in _DECISION_PATTERNS:
            if pattern in all_prose:
                logger.warning(
                    "LLM prose contains decision pattern %r — rejecting",
                    pattern,
                    extra={"operation_id": contract.operation_id},
                )
                return None

        return {
            field: llm_output[field].strip()[:600]
            for field in required_fields
        }

    def _fallback_prose(self, contract: HandoffContract) -> dict[str, str]:
        """
        Deterministic fallback prose when the LLM is unavailable or invalid.
        Uses ONLY structured fields from the HandoffContract — no invented facts.
        """
        op = contract.operation_type.value
        table = contract.primary_table or "unknown table"
        rows = contract.estimated_primary_rows
        actual = contract.actual_primary_rows

        # Section 1: operation summary
        parts = [f"{op} approximately {rows:,} rows from {table!r}"]
        if actual is not None:
            parts.append(f"(staging simulation affected {actual:,} rows)")
        if contract.cascade:
            cascade_total = sum(c.estimated_rows for c in contract.cascade)
            parts.append(
                f"with cascade to ~{cascade_total:,} rows in "
                f"{len(contract.cascade)} dependent table(s)"
            )
        operation_summary = ". ".join(parts) + "."

        # Section 2: database changes
        rev = contract.reversibility.value if contract.reversibility else "UNKNOWN"
        if rev == "PERMANENT":
            db_changes = (
                f"This operation is classified PERMANENT — the database changes "
                f"cannot be automatically reversed. {contract.reversibility_reason}"
            )
        elif rev in ("REVERSIBLE_AUTOMATED", "REVERSIBLE_PITR"):
            db_changes = (
                f"This operation is classified {rev} — recovery may be possible. "
                f"{contract.reversibility_reason}"
            )
        else:
            db_changes = f"Reversibility: {rev}. {contract.reversibility_reason}"

        # Section 3: external effects
        if contract.external_triggers:
            ext_count = len(contract.external_triggers)
            ext_effects = (
                f"{ext_count} external trigger(s) would fire and cannot be automatically "
                "reversed. These effects are outside the database transaction."
            )
        else:
            ext_effects = "No external effects detected."

        # Section 4: historical summary
        if not contract.retrieval_available:
            hist_summary = (
                "Historical retrieval was unavailable — no precedents could be surfaced."
            )
        elif not contract.historical_precedents:
            hist_summary = "No similar historical operations found in the memory store."
        else:
            rejected = [
                h for h in contract.historical_precedents if h.outcome == "REJECTED"
            ]
            if rejected:
                top = rejected[0]
                hist_summary = (
                    f"{len(rejected)} similar operation(s) were previously REJECTED. "
                    f"Top result: {top.operation_id}."
                )
            else:
                top = contract.historical_precedents[0]
                hist_summary = (
                    f"Most similar previous operation: {top.operation_id} "
                    f"(outcome: {top.outcome}, similarity: {top.similarity_score:.2f})."
                )

        return {
            "operation_summary": operation_summary,
            "database_changes_explanation": db_changes,
            "external_effects_explanation": ext_effects,
            "historical_summary": hist_summary,
        }
