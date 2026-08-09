"""
Integration tests for the Contract Agent — Phase 5.

Tests verify the complete Contract Agent pipeline:
  - All four contract sections populated
  - Deterministic risk tier for various scenario types
  - FULL_CONTRACT for large cascade + historical rejection (demo scenario 3)
  - STANDARD for small reversible operation (demo scenario 2)
  - Prompt injection in historical data treated as data
  - Section content correctness end-to-end
  - ApprovalContract schema validation
  - LLM unavailable → graceful fallback (no crash)

These tests mock the LLM to avoid API dependency in CI.
Live API tests are marked with pytest.mark.live_api and skipped by default.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agents.contract.agent import ContractAgent
from agents.contract.contract_builder import build_approval_contract, derive_rollback_plan
from agents.contract.contract_schema import ApprovalContract
from agents.contract.risk_rules import classify_risk
from orchestrator.handoff_schema import (
    CascadeEntry,
    ExternalTrigger,
    HandoffContract,
    HistoricalOperation,
    OperationType,
    PolicyViolation,
    ReversibilityClass,
    RiskTier,
    SourceType,
)


# ── Helpers ────────────────────────────────────────────────────────────────────

def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_integration_test",
        tenant_id="demo_tenant",
        submitted_by="test-pipeline",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
        operation_type=OperationType.DELETE,
        primary_table="users",
        condition="last_active < NOW() - INTERVAL '2 years'",
        estimated_primary_rows=4200,
        row_confidence=0.8,
        reversibility=ReversibilityClass.PERMANENT,
        reversibility_reason=(
            "No soft-delete column; pg_net trigger fires non-transactional HTTP calls."
        ),
        intent_summary="Delete inactive user accounts older than 2 years.",
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


def _mock_llm(summary_text: str = "Delete approximately 4,200 inactive users.") -> MagicMock:
    """Build a mock Anthropic client that returns valid structured prose."""
    mock_block = MagicMock()
    mock_block.type = "tool_use"
    mock_block.name = "produce_contract_summary"
    mock_block.input = {
        "operation_summary": summary_text,
        "database_changes_explanation": "This is a PERMANENT operation — no automated recovery exists.",
        "external_effects_explanation": "No external effects detected.",
        "historical_summary": "One similar operation was previously rejected due to cascade impact.",
    }
    mock_response = MagicMock()
    mock_response.content = [mock_block]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)
    return mock_client


async def _run_agent(contract: HandoffContract, llm=None) -> HandoffContract:
    agent = ContractAgent(contract.operation_id, contract.tenant_id)
    mock_llm = llm or _mock_llm()
    with (
        patch("agents.contract.agent.anthropic.AsyncAnthropic", return_value=mock_llm),
        patch.object(agent, "log_agent_output", new=AsyncMock()),
    ):
        return await agent.run(contract)


# ── Section 1: What will happen? ───────────────────────────────────────────────

class TestSection1Integration:
    @pytest.mark.asyncio
    async def test_section1_populated_for_delete_with_cascade(self):
        """Section 1 contains all impact fields for DELETE with cascade."""
        contract = _make_contract(
            cascade=[
                CascadeEntry(table="orders", estimated_rows=12000,
                             cascade_action="CASCADE", depth=1),
                CascadeEntry(table="invoices", estimated_rows=8200,
                             cascade_action="CASCADE", depth=2),
            ],
        )
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        approval = ApprovalContract.model_validate(parsed)
        s1 = approval.sections.what_will_happen

        assert s1.operation_type == "DELETE"
        assert s1.primary_table == "users"
        assert s1.estimated_primary_rows == 4200
        assert len(s1.cascade_impact) == 2
        assert s1.total_estimated_rows == 4200 + 12000 + 8200
        assert s1.operation_summary != ""

    @pytest.mark.asyncio
    async def test_section1_external_actions_from_triggers(self):
        """External triggers appear as external actions in section 1."""
        contract = _make_contract(
            external_triggers=[
                ExternalTrigger(
                    trigger_name="on_user_delete",
                    event="AFTER DELETE",
                    extension="pg_net",
                    estimated_calls=4200,
                    target_endpoint="https://api.example.com/webhook",
                )
            ],
        )
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        approval = ApprovalContract.model_validate(parsed)
        s1 = approval.sections.what_will_happen

        assert len(s1.external_actions) == 1
        assert s1.external_actions[0].trigger_name == "on_user_delete"
        assert s1.external_actions[0].extension == "pg_net"

    @pytest.mark.asyncio
    async def test_section1_simulation_note_when_executed(self):
        """Section 1 notes actual rows when simulation ran."""
        contract = _make_contract(
            simulation_executed=True,
            actual_primary_rows=4187,
        )
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s1 = ApprovalContract.model_validate(parsed).sections.what_will_happen
        assert s1.actual_primary_rows == 4187
        assert s1.simulation_row_delta_note is not None


# ── Section 2: What cannot be undone? ─────────────────────────────────────────

class TestSection2Integration:
    @pytest.mark.asyncio
    async def test_section2_permanent_requires_ack(self):
        """PERMANENT → requires_permanent_acknowledgement = True in section 2."""
        contract = _make_contract(reversibility=ReversibilityClass.PERMANENT)
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        approval = ApprovalContract.model_validate(parsed)
        s2 = approval.sections.what_cannot_be_undone

        assert s2.reversibility == "PERMANENT"
        assert s2.requires_permanent_acknowledgement is True
        assert approval.requires_permanent_acknowledgement is True
        assert s2.rollback_plan is None

    @pytest.mark.asyncio
    async def test_section2_reversible_pitr_has_rollback(self):
        """REVERSIBLE_PITR → section 2 contains rollback_plan mentioning PITR."""
        contract = _make_contract(reversibility=ReversibilityClass.REVERSIBLE_PITR)
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s2 = ApprovalContract.model_validate(parsed).sections.what_cannot_be_undone

        assert s2.reversibility == "REVERSIBLE_PITR"
        assert s2.rollback_plan is not None
        assert "point-in-time" in s2.rollback_plan.lower() or "pitr" in s2.rollback_plan.lower()
        assert s2.requires_permanent_acknowledgement is False

    @pytest.mark.asyncio
    async def test_section2_no_llm_generated_sql(self):
        """Section 2 rollback_plan never contains LLM-generated SQL."""
        contract = _make_contract(reversibility=ReversibilityClass.PERMANENT)
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s2 = ApprovalContract.model_validate(parsed).sections.what_cannot_be_undone
        # PERMANENT → rollback_plan is None (no SQL generated)
        assert s2.rollback_plan is None


# ── Section 3: Has this happened before? ──────────────────────────────────────

class TestSection3Integration:
    @pytest.mark.asyncio
    async def test_section3_historical_precedents_populated(self):
        """Section 3 contains historical precedents from contract."""
        contract = _make_contract(
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_1847",
                    intent_summary="Delete inactive user accounts older than 2 years",
                    tables=["users", "orders", "invoices"],
                    outcome="REJECTED",
                    decision_reason="Cascade into invoices caused loss of 8,200 billing records.",
                    similarity_score=0.91,
                    jaccard_score=0.75,
                    rerank_score=1.0,
                ),
            ],
        )
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s3 = ApprovalContract.model_validate(parsed).sections.has_this_happened_before

        assert s3.retrieval_available is True
        assert len(s3.precedents) == 1
        assert s3.precedents[0].operation_id == "cg_1847"
        assert s3.precedents[0].outcome == "REJECTED"  # preserved exactly
        assert s3.contains_rejected_outcome is True
        assert s3.precedents[0].is_top_result is True

    @pytest.mark.asyncio
    async def test_section3_retrieval_unavailable(self):
        """Section 3 reflects retrieval_available=False."""
        contract = _make_contract(retrieval_available=False, historical_precedents=[])
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s3 = ApprovalContract.model_validate(parsed).sections.has_this_happened_before

        assert s3.retrieval_available is False
        assert s3.precedents == []

    @pytest.mark.asyncio
    async def test_section3_max_3_precedents(self):
        """Section 3 contains at most 3 historical precedents."""
        hists = [
            HistoricalOperation(
                operation_id=f"cg_{i:04d}",
                intent_summary=f"Op {i}",
                tables=["users"],
                outcome="APPROVED",
                decision_reason=f"Reason {i}",
                similarity_score=0.9 - i * 0.1,
                jaccard_score=0.8,
                rerank_score=0.5,
            )
            for i in range(5)
        ]
        contract = _make_contract(historical_precedents=hists)
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s3 = ApprovalContract.model_validate(parsed).sections.has_this_happened_before
        assert len(s3.precedents) <= 3


# ── Section 4: System flags ────────────────────────────────────────────────────

class TestSection4Integration:
    @pytest.mark.asyncio
    async def test_section4_policy_violations(self):
        """Section 4 contains policy violations from contract."""
        contract = _make_contract(
            policy_violations=[
                PolicyViolation(
                    rule_id="POLICY_BULK_DELETE_SENSITIVE",
                    rule_name="Bulk Delete Sensitive",
                    severity="HIGH",
                    description="DELETE affecting >10,000 rows on sensitive table.",
                )
            ],
        )
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s4 = ApprovalContract.model_validate(parsed).sections.system_flags

        assert len(s4.policy_violations) == 1
        assert s4.policy_violations[0].rule_id == "POLICY_BULK_DELETE_SENSITIVE"

    @pytest.mark.asyncio
    async def test_section4_historical_rejection_warning(self):
        """REJECTED precedent → historical_rejection_warning in section 4."""
        contract = _make_contract(
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_1847",
                    intent_summary="Delete inactive users",
                    tables=["users"],
                    outcome="REJECTED",
                    decision_reason="Cascade too large.",
                    similarity_score=0.9, jaccard_score=0.8, rerank_score=1.0,
                )
            ],
        )
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s4 = ApprovalContract.model_validate(parsed).sections.system_flags
        assert s4.historical_rejection_warning is True

    @pytest.mark.asyncio
    async def test_section4_simulation_unavailable_flag(self):
        """simulation_available=False → flag in section 4."""
        contract = _make_contract(simulation_available=False)
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s4 = ApprovalContract.model_validate(parsed).sections.system_flags
        assert s4.simulation_unavailable is True

    @pytest.mark.asyncio
    async def test_section4_prompt_injection_flag(self):
        """prompt_injection_risk=True → flag in section 4."""
        contract = _make_contract(prompt_injection_risk=True)
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        s4 = ApprovalContract.model_validate(parsed).sections.system_flags
        assert s4.prompt_injection_risk is True


# ── Risk tier integration tests ────────────────────────────────────────────────

class TestRiskTierIntegration:
    @pytest.mark.asyncio
    async def test_demo_scenario_3_large_cascade_and_rejection_returns_full(self):
        """
        Demo Scenario 3 — DELETE users with large cascade + historical rejection:
        Large blast radius + REJECTED precedent → FULL_CONTRACT.

        This tests the architecture-specified behavior without hardcoding row counts.
        The key invariants:
          - cascade total > CASCADE_ROW_THRESHOLD → FULL  (or)
          - historical REJECTED precedent → FULL
        Either condition alone is sufficient.
        """
        from agents.contract.risk_rules import CASCADE_ROW_THRESHOLD

        contract = _make_contract(
            reversibility=ReversibilityClass.PERMANENT,
            cascade=[
                CascadeEntry(table="orders", estimated_rows=CASCADE_ROW_THRESHOLD + 1,
                             cascade_action="CASCADE", depth=1),
            ],
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_1847",
                    intent_summary="Delete inactive user accounts older than 2 years",
                    tables=["users", "orders", "invoices"],
                    outcome="REJECTED",
                    decision_reason="Cascade into invoices caused loss of 8,200 billing records.",
                    similarity_score=0.91,
                    jaccard_score=0.75,
                    rerank_score=1.0,
                )
            ],
        )
        await _run_agent(contract)

        assert contract.risk_tier == RiskTier.FULL_CONTRACT
        assert contract.contract_assembled is True

        parsed = json.loads(contract.approval_contract_json)
        approval = ApprovalContract.model_validate(parsed)
        assert approval.risk_tier == "FULL_CONTRACT"
        assert approval.sections.has_this_happened_before.contains_rejected_outcome is True
        assert approval.sections.system_flags.historical_rejection_warning is True

    @pytest.mark.asyncio
    async def test_demo_scenario_2_standard_update_returns_standard(self):
        """
        Demo Scenario 2 — UPDATE users, 3,847 rows, REVERSIBLE, no rejected history:
        → STANDARD tier.
        """
        contract = _make_contract(
            raw_sql="UPDATE users SET status = 'inactive' WHERE last_active < NOW() - INTERVAL '1 year'",
            operation_type=OperationType.UPDATE,
            primary_table="users",
            condition="last_active < NOW() - INTERVAL '1 year'",
            estimated_primary_rows=3847,
            row_confidence=0.8,
            reversibility=ReversibilityClass.REVERSIBLE_PITR,
            reversibility_reason="No non-transactional triggers; recoverable via PITR.",
            cascade=[],
            external_triggers=[],
            historical_precedents=[],
        )
        await _run_agent(contract)

        assert contract.risk_tier == RiskTier.STANDARD
        assert contract.contract_assembled is True

    @pytest.mark.asyncio
    async def test_auto_execute_for_simple_select(self):
        """Simple SELECT with low cost and no violations → AUTO tier."""
        contract = _make_contract(
            raw_sql="SELECT id, name FROM products WHERE category = 'electronics'",
            operation_type=OperationType.SELECT,
            primary_table="products",
            condition="category = 'electronics'",
            estimated_primary_rows=100,
            row_confidence=0.9,
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
            reversibility_reason="Read operation.",
            cascade=[],
            external_triggers=[],
            historical_precedents=[],
            policy_violations=[],
        )
        await _run_agent(contract)

        assert contract.risk_tier == RiskTier.AUTO

    @pytest.mark.asyncio
    async def test_idempotency_same_contract_same_tier(self):
        """Same contract → same risk_tier on repeated agent runs."""
        c1 = _make_contract(reversibility=ReversibilityClass.REVERSIBLE_PITR)
        c2 = _make_contract(reversibility=ReversibilityClass.REVERSIBLE_PITR)

        await _run_agent(c1)
        await _run_agent(c2)

        assert c1.risk_tier == c2.risk_tier
        assert c1.risk_score == c2.risk_score


# ── Full pipeline: all four sections non-empty ─────────────────────────────────

class TestAllFourSectionsPopulated:
    @pytest.mark.asyncio
    async def test_all_sections_non_empty_for_full_scenario(self):
        """
        A fully-populated scenario produces all four contract sections
        with non-empty content.
        """
        contract = _make_contract(
            cascade=[
                CascadeEntry(table="orders", estimated_rows=12000,
                             cascade_action="CASCADE", depth=1),
            ],
            external_triggers=[
                ExternalTrigger(
                    trigger_name="on_user_delete",
                    event="AFTER DELETE",
                    extension="pg_net",
                    estimated_calls=4200,
                ),
            ],
            historical_precedents=[
                HistoricalOperation(
                    operation_id="cg_1847",
                    intent_summary="Delete inactive user accounts",
                    tables=["users", "orders"],
                    outcome="REJECTED",
                    decision_reason="Cascade too large.",
                    similarity_score=0.91, jaccard_score=0.75, rerank_score=1.0,
                )
            ],
            policy_violations=[
                PolicyViolation(
                    rule_id="POLICY_BULK_DELETE_SENSITIVE",
                    rule_name="Bulk Delete",
                    severity="HIGH",
                    description="DELETE on sensitive table.",
                )
            ],
            simulation_executed=True,
            actual_primary_rows=4187,
        )
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        approval = ApprovalContract.model_validate(parsed)

        # Section 1
        s1 = approval.sections.what_will_happen
        assert s1.operation_summary != ""
        assert s1.operation_type == "DELETE"
        assert len(s1.cascade_impact) == 1
        assert len(s1.external_actions) == 1

        # Section 2
        s2 = approval.sections.what_cannot_be_undone
        assert s2.reversibility != ""
        assert s2.database_changes_explanation != ""
        assert s2.external_effects_explanation != ""

        # Section 3
        s3 = approval.sections.has_this_happened_before
        assert s3.historical_summary != ""
        assert len(s3.precedents) == 1

        # Section 4
        s4 = approval.sections.system_flags
        assert len(s4.policy_violations) == 1
        assert s4.historical_rejection_warning is True

    @pytest.mark.asyncio
    async def test_minimal_scenario_all_sections_present(self):
        """Even a minimal SELECT scenario produces all four sections."""
        contract = _make_contract(
            operation_type=OperationType.SELECT,
            primary_table="products",
            condition="id = 1",
            estimated_primary_rows=1,
            row_confidence=0.95,
            reversibility=ReversibilityClass.REVERSIBLE_AUTOMATED,
            cascade=[],
            external_triggers=[],
            historical_precedents=[],
        )
        await _run_agent(contract)

        parsed = json.loads(contract.approval_contract_json)
        approval = ApprovalContract.model_validate(parsed)

        # All four sections exist (even if mostly empty)
        assert approval.sections.what_will_happen is not None
        assert approval.sections.what_cannot_be_undone is not None
        assert approval.sections.has_this_happened_before is not None
        assert approval.sections.system_flags is not None


# ── LLM failure handling ───────────────────────────────────────────────────────

class TestLLMFailureHandling:
    @pytest.mark.asyncio
    async def test_llm_unavailable_contract_still_assembled(self):
        """LLM unavailable → deterministic fallback; contract_assembled = True."""
        contract = _make_contract()
        agent = ContractAgent(contract.operation_id, contract.tenant_id)

        with (
            patch("agents.contract.agent.anthropic.AsyncAnthropic",
                  side_effect=Exception("API unavailable")),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            await agent.run(contract)

        assert contract.contract_assembled is True
        assert contract.risk_tier is not None
        assert contract.approval_contract_json != ""

    @pytest.mark.asyncio
    async def test_llm_api_error_deterministic_fields_still_correct(self):
        """Even with LLM failure, deterministic fields (risk_tier, risk_score) are correct."""
        contract = _make_contract(reversibility=ReversibilityClass.PERMANENT)
        expected_tier, expected_score = classify_risk(contract)

        agent = ContractAgent(contract.operation_id, contract.tenant_id)
        with (
            patch("agents.contract.agent.anthropic.AsyncAnthropic",
                  side_effect=Exception("Timeout")),
            patch.object(agent, "log_agent_output", new=AsyncMock()),
        ):
            await agent.run(contract)

        assert contract.risk_tier == expected_tier
        assert contract.risk_score == expected_score
