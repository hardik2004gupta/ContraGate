"""
Tests for explain_parser.py — pure unit tests, no database required.

Covers:
  - Basic Seq Scan output (no ANALYZE)
  - ANALYZE output with actual_rows and execution_time_ms
  - BUFFERS output with shared_hit_blocks / shared_read_blocks
  - Partitioned table (Append node) → partitions_involved count
  - High-cost query → total_cost > threshold
  - Nested plan (Hash Join with children) → scan_type = top-level node type
  - List vs dict input to parse_explain_json
  - Robustness: empty list, missing Plan, malformed node
  - Security: multi-statement SQL injection rejected by _validate_explain_target
"""

from __future__ import annotations

import pytest

from sql_analysis_lib.explain_parser import (
    ExplainResult,
    _validate_explain_target,
    parse_explain_json,
)


# ── Fixture helpers ──────────────────────────────────────────────────────────

def _seq_scan_basic() -> dict:
    return {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "cg_users",
            "Startup Cost": 0.00,
            "Total Cost": 18.50,
            "Plan Rows": 850,
            "Plan Width": 36,
        },
        "Planning Time": 0.123,
    }


def _seq_scan_analyze() -> dict:
    return {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "cg_users",
            "Startup Cost": 0.00,
            "Total Cost": 18.50,
            "Plan Rows": 850,
            "Plan Width": 36,
            "Actual Startup Time": 0.025,
            "Actual Rows": 1000,
            "Actual Loops": 1,
            "Shared Hit Blocks": 8,
            "Shared Read Blocks": 0,
        },
        "Planning Time": 0.120,
        "Execution Time": 0.456,
    }


def _hash_join() -> dict:
    return {
        "Plan": {
            "Node Type": "Hash Join",
            "Startup Cost": 5.00,
            "Total Cost": 45.00,
            "Plan Rows": 200,
            "Plan Width": 64,
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "cg_orders",
                    "Startup Cost": 0.00,
                    "Total Cost": 15.00,
                    "Plan Rows": 400,
                    "Plan Width": 32,
                },
                {
                    "Node Type": "Hash",
                    "Startup Cost": 2.00,
                    "Total Cost": 8.00,
                    "Plan Rows": 100,
                    "Plan Width": 32,
                    "Plans": [
                        {
                            "Node Type": "Seq Scan",
                            "Relation Name": "cg_users",
                            "Startup Cost": 0.00,
                            "Total Cost": 6.00,
                            "Plan Rows": 100,
                            "Plan Width": 16,
                        }
                    ],
                },
            ],
        },
        "Planning Time": 0.250,
    }


def _partitioned_append() -> dict:
    return {
        "Plan": {
            "Node Type": "Append",
            "Startup Cost": 0.00,
            "Total Cost": 120.00,
            "Plan Rows": 5000,
            "Plan Width": 36,
            "Plans": [
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "cg_orders_2022",
                    "Startup Cost": 0.00,
                    "Total Cost": 30.00,
                    "Plan Rows": 1200,
                    "Plan Width": 36,
                },
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "cg_orders_2023",
                    "Startup Cost": 0.00,
                    "Total Cost": 40.00,
                    "Plan Rows": 1800,
                    "Plan Width": 36,
                },
                {
                    "Node Type": "Seq Scan",
                    "Relation Name": "cg_orders_2024",
                    "Startup Cost": 0.00,
                    "Total Cost": 50.00,
                    "Plan Rows": 2000,
                    "Plan Width": 36,
                },
            ],
        },
        "Planning Time": 0.350,
    }


def _high_cost() -> dict:
    return {
        "Plan": {
            "Node Type": "Seq Scan",
            "Relation Name": "cg_txn_log",
            "Startup Cost": 0.00,
            "Total Cost": 187543.20,
            "Plan Rows": 10000000,
            "Plan Width": 48,
        },
        "Planning Time": 0.500,
    }


# ── Tests: parse_explain_json ────────────────────────────────────────────────

class TestParseExplainJsonBasic:
    def test_seq_scan_fields(self):
        result = parse_explain_json(_seq_scan_basic())
        assert isinstance(result, ExplainResult)
        assert result.estimated_rows == 850
        assert result.total_cost == pytest.approx(18.50)
        assert result.startup_cost == pytest.approx(0.00)
        assert result.scan_type == "Seq Scan"
        assert result.partitions_involved == 0

    def test_no_analyze_fields_are_none(self):
        result = parse_explain_json(_seq_scan_basic())
        assert result.actual_rows is None
        assert result.execution_time_ms is None
        assert result.shared_hit_blocks is None
        assert result.shared_read_blocks is None

    def test_planning_time_present(self):
        result = parse_explain_json(_seq_scan_basic())
        assert result.planning_time_ms == pytest.approx(0.123)

    def test_accepts_list_input(self):
        """parse_explain_json must accept the raw psycopg2 list format."""
        result = parse_explain_json([_seq_scan_basic()])
        assert result.estimated_rows == 850


class TestParseExplainJsonAnalyze:
    def test_actual_rows_populated(self):
        result = parse_explain_json(_seq_scan_analyze())
        assert result.actual_rows == 1000

    def test_execution_time_populated(self):
        result = parse_explain_json(_seq_scan_analyze())
        assert result.execution_time_ms == pytest.approx(0.456)

    def test_buffer_blocks_populated(self):
        result = parse_explain_json(_seq_scan_analyze())
        assert result.shared_hit_blocks == 8
        assert result.shared_read_blocks == 0


class TestParseExplainJsonPartitions:
    def test_partitions_counted_from_append_children(self):
        result = parse_explain_json(_partitioned_append())
        assert result.partitions_involved == 3

    def test_partitioned_scan_type_is_append(self):
        result = parse_explain_json(_partitioned_append())
        assert result.scan_type == "Append"

    def test_non_partitioned_returns_zero(self):
        result = parse_explain_json(_seq_scan_basic())
        assert result.partitions_involved == 0


class TestParseExplainJsonNestedPlan:
    def test_scan_type_is_top_level_node(self):
        """Hash Join with Seq Scan children — scan_type should be Hash Join."""
        result = parse_explain_json(_hash_join())
        assert result.scan_type == "Hash Join"

    def test_estimated_rows_from_top_level(self):
        result = parse_explain_json(_hash_join())
        assert result.estimated_rows == 200

    def test_no_append_in_nested_returns_zero_partitions(self):
        result = parse_explain_json(_hash_join())
        assert result.partitions_involved == 0


class TestParseExplainJsonHighCost:
    def test_high_cost_above_read_impact_threshold(self):
        """Total cost > 50,000 triggers the Read Impact Gate in production."""
        result = parse_explain_json(_high_cost())
        assert result.total_cost > 50_000
        assert result.estimated_rows == 10_000_000

    def test_high_cost_no_partitions(self):
        result = parse_explain_json(_high_cost())
        assert result.partitions_involved == 0


class TestParseExplainJsonRobustness:
    """Malformed input must raise ValueError, not crash with AttributeError/KeyError."""

    def test_empty_list_raises_value_error(self):
        with pytest.raises(ValueError, match="empty list"):
            parse_explain_json([])

    def test_missing_plan_key_raises_value_error(self):
        with pytest.raises(ValueError, match="missing 'Plan' key"):
            parse_explain_json({"Planning Time": 0.1})

    def test_plan_is_not_dict_raises_value_error(self):
        with pytest.raises(ValueError, match="must be a dict"):
            parse_explain_json({"Plan": "not-a-dict"})

    def test_non_dict_element_in_list_raises_value_error(self):
        with pytest.raises((ValueError, AttributeError, TypeError)):
            parse_explain_json(["not-a-dict"])

    def test_missing_optional_fields_defaults(self):
        """Missing optional fields in Plan should not raise — defaults are used."""
        minimal = {"Plan": {"Node Type": "Seq Scan"}}
        result = parse_explain_json(minimal)
        assert result.estimated_rows == 0
        assert result.total_cost == pytest.approx(0.0)
        assert result.scan_type == "Seq Scan"
        assert result.actual_rows is None
        assert result.shared_hit_blocks is None
        assert result.shared_read_blocks is None
        assert result.execution_time_ms is None
        assert result.planning_time_ms is None


# ── Tests: _validate_explain_target (SQL injection prevention) ───────────────

class TestValidateExplainTarget:
    """
    Security tests for _validate_explain_target().

    These verify that multi-statement payloads and non-SELECT statements are
    rejected before reaching the database. SQL is treated as DATA.
    """

    def test_valid_simple_select(self):
        _validate_explain_target("SELECT * FROM cg_users")

    def test_valid_select_with_where(self):
        _validate_explain_target("SELECT 1 FROM cg_users WHERE id = 1")

    def test_valid_with_cte(self):
        _validate_explain_target("WITH x AS (SELECT 1) SELECT * FROM x")

    def test_valid_values(self):
        _validate_explain_target("VALUES (1), (2)")

    def test_multi_statement_semicolon_rejected(self):
        with pytest.raises(ValueError, match="single statement"):
            _validate_explain_target("SELECT 1; DELETE FROM users")

    def test_multi_statement_update_rejected(self):
        with pytest.raises(ValueError, match="single statement"):
            _validate_explain_target("SELECT 1; UPDATE users SET x = 1")

    def test_multi_statement_drop_rejected(self):
        with pytest.raises(ValueError, match="single statement"):
            _validate_explain_target("SELECT 1; DROP TABLE users")

    def test_delete_statement_rejected(self):
        with pytest.raises(ValueError, match="SELECT statement"):
            _validate_explain_target("DELETE FROM users")

    def test_update_statement_rejected(self):
        with pytest.raises(ValueError, match="SELECT statement"):
            _validate_explain_target("UPDATE users SET x = 1")

    def test_insert_statement_rejected(self):
        with pytest.raises(ValueError, match="SELECT statement"):
            _validate_explain_target("INSERT INTO users VALUES (1)")

    def test_drop_statement_rejected(self):
        with pytest.raises(ValueError, match="SELECT statement"):
            _validate_explain_target("DROP TABLE users")

    def test_empty_sql_rejected(self):
        with pytest.raises(ValueError):
            _validate_explain_target("")

    def test_whitespace_only_sql_rejected(self):
        with pytest.raises(ValueError):
            _validate_explain_target("   \n  ")

    def test_semicolon_in_string_literal_is_allowed(self):
        """A semicolon inside a string literal is not a statement separator."""
        _validate_explain_target("SELECT * FROM t WHERE note = 'a; b'")

    def test_semicolon_in_line_comment_is_allowed(self):
        """A semicolon inside a comment is not a statement separator."""
        _validate_explain_target("SELECT 1 -- this comment has a ; semicolon\nFROM t")

    def test_semicolon_in_block_comment_is_allowed(self):
        """A semicolon inside a block comment is not a statement separator."""
        _validate_explain_target("SELECT 1 /* comment; not a separator */ FROM t")
