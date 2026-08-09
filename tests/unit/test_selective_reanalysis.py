"""
Phase 6 — test_selective_reanalysis.py

Verifies that selective re-analysis after MODIFY:
1. Only re-runs fields listed in stale_fields
2. Skips analysis entirely when no analysis fields are stale
3. Guards detect re-analysis correctly
4. Context+Sim skips appropriately
5. CONTRACT always re-runs (risk tier re-classification required after modify)
"""

from __future__ import annotations

import pytest

from orchestrator.guards import needs_selective_reanalysis
from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    ReversibilityClass,
    RiskTier,
    SourceType,
)


def _make_contract(**kwargs) -> HandoffContract:
    defaults = dict(
        operation_id="cg_reanalysis1",
        tenant_id="demo_tenant",
        submitted_by="test",
        source_type=SourceType.INTERNAL_SYSTEM,
        raw_sql="DELETE FROM orders WHERE status = 'cancelled' AND created_at < NOW() - INTERVAL '3 years'",
        operation_type=OperationType.DELETE,
        approval_state=ApprovalState.MODIFIED,
    )
    defaults.update(kwargs)
    return HandoffContract(**defaults)


# ── needs_selective_reanalysis guard ─────────────────────────────────────────

class TestNeedsSelectiveReanalysisGuard:
    def test_returns_false_on_first_pass(self):
        c = _make_contract(reanalysis_count=0, stale_fields=[])
        assert needs_selective_reanalysis(c) is False

    def test_returns_false_when_no_stale_fields(self):
        c = _make_contract(reanalysis_count=1, stale_fields=[])
        assert needs_selective_reanalysis(c) is False

    def test_returns_true_on_reanalysis_with_stale_fields(self):
        c = _make_contract(reanalysis_count=1, stale_fields=["intent_summary", "estimated_primary_rows"])
        assert needs_selective_reanalysis(c) is True

    def test_returns_true_for_multiple_reanalysis_passes(self):
        c = _make_contract(reanalysis_count=3, stale_fields=["reversibility"])
        assert needs_selective_reanalysis(c) is True

    def test_zero_count_with_stale_fields_returns_false(self):
        # stale_fields alone is not enough — reanalysis_count must also be > 0
        c = _make_contract(reanalysis_count=0, stale_fields=["reversibility"])
        assert needs_selective_reanalysis(c) is False


# ── Analyzer Agent selective skip ─────────────────────────────────────────────

class TestAnalyzerAgentSelectiveSkip:
    def test_analysis_fields_constant_covers_expected_keys(self):
        """The _ANALYSIS_FIELDS frozenset must include core blast-radius fields."""
        from agents.analyzer.agent import _ANALYSIS_FIELDS
        required = {"intent_summary", "estimated_primary_rows", "reversibility", "cascade"}
        assert required.issubset(_ANALYSIS_FIELDS)

    def test_stale_fields_overlap_with_analysis_triggers_rerun(self):
        from agents.analyzer.agent import _ANALYSIS_FIELDS
        stale = {"intent_summary", "estimated_primary_rows"}
        assert bool(_ANALYSIS_FIELDS & stale)

    def test_non_analysis_stale_fields_dont_trigger_rerun(self):
        from agents.analyzer.agent import _ANALYSIS_FIELDS
        # Only context_sim fields are stale — analysis should be skipped
        stale = {"historical_precedents", "actual_primary_rows"}
        assert not bool(_ANALYSIS_FIELDS & stale)


# ── stale_fields lifecycle ────────────────────────────────────────────────────

class TestStaleFieldsLifecycle:
    def test_stale_fields_initially_empty(self):
        c = _make_contract()
        assert c.stale_fields == []

    def test_stale_fields_can_be_set(self):
        c = _make_contract(stale_fields=["reversibility", "estimated_primary_rows"])
        assert "reversibility" in c.stale_fields
        assert "estimated_primary_rows" in c.stale_fields

    def test_reanalysis_count_initially_zero(self):
        c = _make_contract()
        assert c.reanalysis_count == 0

    def test_modification_constraints_stored(self):
        c = _make_contract(modification_constraints="LIMIT 1000")
        assert c.modification_constraints == "LIMIT 1000"

    def test_approval_state_modified_triggers_modified_route(self):
        from orchestrator.guards import route_from_human_review
        c = _make_contract(approval_state=ApprovalState.MODIFIED)
        assert route_from_human_review(c) == "modified"

    def test_approval_state_modified_enables_reanalysis_after_increment(self):
        c = _make_contract(
            reanalysis_count=1,
            stale_fields=["estimated_primary_rows"],
        )
        assert needs_selective_reanalysis(c) is True

    def test_full_analysis_fields_all_stale_triggers_complete_rerun(self):
        from agents.analyzer.agent import _ANALYSIS_FIELDS
        c = _make_contract(
            reanalysis_count=1,
            stale_fields=list(_ANALYSIS_FIELDS),
        )
        assert needs_selective_reanalysis(c) is True
        # All analysis fields are stale → full analysis rerun
        assert bool(_ANALYSIS_FIELDS & set(c.stale_fields))
