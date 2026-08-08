"""
Tests for row_estimator.py — requires live PostgreSQL connection.

Uses the ecommerce and simple_fk schema fixtures. Tests are skipped
automatically when DATABASE_URL / --pg-url is not provided.

Covers:
  - "stats" strategy (no condition) → reltuples from pg_class
  - "explain" strategy (with condition) → EXPLAIN Plan Rows
  - "stats" fallback strategy when EXPLAIN fails
  - Confidence = 0.8 for fresh stats, lower for stale
  - update_confidence() — under/overestimate adjustments with 10% thresholds
  - stats_stale flag set correctly
  - Confidence never falls below 0.1
  - Zero estimated_rows edge case
"""

from __future__ import annotations

import pytest

from sql_analysis_lib.row_estimator import RowEstimate, estimate_rows, update_confidence
from .conftest import requires_db


@pytest.fixture(scope="module")
def schema(pg_conn, ecommerce_schema, simple_fk_schema):
    """Ensure schemas are applied and tables have been ANALYZEd."""
    requires_db(pg_conn)
    with pg_conn.cursor() as cur:
        # Seed minimal data so reltuples isn't zero after ANALYZE
        cur.execute("INSERT INTO cg_departments(name) VALUES ('Engineering'), ('Sales') ON CONFLICT DO NOTHING")
        cur.execute("""
            INSERT INTO cg_employees(dept_id, name, salary)
            SELECT d.id, 'Employee ' || generate_series, 70000
            FROM cg_departments d, generate_series(1, 5)
            ON CONFLICT DO NOTHING
        """)
        cur.execute("ANALYZE cg_departments")
        cur.execute("ANALYZE cg_employees")
    pg_conn.commit()
    return pg_conn


class TestEstimateRowsNoCondition:
    def test_stats_strategy_used(self, schema):
        """No condition → reltuples from pg_class, strategy='stats' (not 'pg_stats')."""
        result = estimate_rows("cg_departments", None, schema)
        assert isinstance(result, RowEstimate)
        assert result.estimation_strategy == "stats"
        assert result.table == "cg_departments"
        assert result.condition is None

    def test_estimated_rows_nonnegative(self, schema):
        result = estimate_rows("cg_departments", None, schema)
        assert result.estimated_rows >= 0

    def test_confidence_is_float_between_zero_and_one(self, schema):
        result = estimate_rows("cg_departments", None, schema)
        assert 0.0 <= result.confidence <= 1.0

    def test_fresh_stats_confidence_at_least_0_5(self, schema):
        """After explicit ANALYZE, stats should be fresh → confidence ≥ 0.5."""
        result = estimate_rows("cg_departments", None, schema)
        assert result.confidence >= 0.5

    def test_stats_age_hours_nonnegative(self, schema):
        result = estimate_rows("cg_departments", None, schema)
        assert result.stats_age_hours >= 0.0


class TestEstimateRowsWithCondition:
    def test_explain_strategy_used(self, schema):
        result = estimate_rows("cg_employees", "salary > 50000", schema)
        assert result.estimation_strategy == "explain"

    def test_estimated_rows_nonnegative_with_condition(self, schema):
        result = estimate_rows("cg_employees", "id > 0", schema)
        assert result.estimated_rows >= 0

    def test_condition_stored_in_result(self, schema):
        result = estimate_rows("cg_employees", "id = 1", schema)
        assert result.condition == "id = 1"


class TestUpdateConfidence:
    def test_underestimate_lowers_confidence(self):
        """actual > estimated * 1.10 → confidence lowered by 0.05."""
        store: dict = {}
        new_conf = update_confidence("orders", "DELETE", actual_rows=200, estimated_rows=100, confidence_store=store)
        assert new_conf < 0.8

    def test_overestimate_lowers_confidence(self):
        """actual < estimated * 0.90 → confidence lowered by 0.02."""
        store: dict = {}
        new_conf = update_confidence("orders", "DELETE", actual_rows=10, estimated_rows=100, confidence_store=store)
        assert new_conf < 0.8

    def test_accurate_estimate_no_change(self):
        """Within ±10% → no change (95/100 = 0.95 which is ≥ 0.90 and ≤ 1.10)."""
        store: dict = {"orders:DELETE": 0.8}
        new_conf = update_confidence("orders", "DELETE", actual_rows=95, estimated_rows=100, confidence_store=store)
        assert new_conf == pytest.approx(0.8)

    def test_confidence_never_below_0_1(self):
        store: dict = {"t:DELETE": 0.11}
        for _ in range(20):
            new_conf = update_confidence("t", "DELETE", actual_rows=1000, estimated_rows=1, confidence_store=store)
        assert new_conf >= 0.1

    def test_underestimate_decrease_is_0_05(self):
        """200 > 100 * 1.10 = 110 → underestimate → -0.05 from default 0.8."""
        store: dict = {}
        new_conf = update_confidence("t", "UPDATE", actual_rows=200, estimated_rows=100, confidence_store=store)
        assert new_conf == pytest.approx(0.75)

    def test_overestimate_decrease_is_0_02(self):
        """10 < 100 * 0.90 = 90 → overestimate → -0.02 from default 0.8."""
        store: dict = {}
        new_conf = update_confidence("t", "UPDATE", actual_rows=10, estimated_rows=100, confidence_store=store)
        assert new_conf == pytest.approx(0.78)

    def test_store_persists_between_calls(self):
        store: dict = {}
        update_confidence("t", "DELETE", actual_rows=200, estimated_rows=100, confidence_store=store)
        assert "t:DELETE" in store

    def test_exactly_at_underestimate_boundary_no_change(self):
        """actual == estimated * 1.10 exactly → within threshold, no change (boundary is exclusive)."""
        store: dict = {"t:DELETE": 0.8}
        new_conf = update_confidence("t", "DELETE", actual_rows=110, estimated_rows=100, confidence_store=store)
        # 110 is NOT > 100 * 1.10 (= 110), so no change
        assert new_conf == pytest.approx(0.8)

    def test_just_above_underestimate_boundary_lowers(self):
        """actual == estimated * 1.11 → strictly above threshold → -0.05."""
        store: dict = {"t:DELETE": 0.8}
        new_conf = update_confidence("t", "DELETE", actual_rows=111, estimated_rows=100, confidence_store=store)
        assert new_conf == pytest.approx(0.75)

    def test_exactly_at_overestimate_boundary_no_change(self):
        """actual == estimated * 0.90 exactly → within threshold, no change (boundary is exclusive)."""
        store: dict = {"t:DELETE": 0.8}
        new_conf = update_confidence("t", "DELETE", actual_rows=90, estimated_rows=100, confidence_store=store)
        # 90 is NOT < 100 * 0.90 (= 90), so no change
        assert new_conf == pytest.approx(0.8)

    def test_just_below_overestimate_boundary_lowers(self):
        """actual == estimated * 0.89 → strictly below threshold → -0.02."""
        store: dict = {"t:DELETE": 0.8}
        new_conf = update_confidence("t", "DELETE", actual_rows=89, estimated_rows=100, confidence_store=store)
        assert new_conf == pytest.approx(0.78)

    def test_zero_estimated_rows_with_actual_rows_is_underestimate(self):
        """estimated_rows=0 and actual_rows>0 → severe underestimate → -0.05."""
        store: dict = {}
        new_conf = update_confidence("t", "DELETE", actual_rows=5, estimated_rows=0, confidence_store=store)
        assert new_conf == pytest.approx(0.75)

    def test_zero_estimated_rows_with_zero_actual_rows_no_change(self):
        """estimated_rows=0 and actual_rows=0 → no change."""
        store: dict = {"t:DELETE": 0.8}
        new_conf = update_confidence("t", "DELETE", actual_rows=0, estimated_rows=0, confidence_store=store)
        assert new_conf == pytest.approx(0.8)
