"""
sql_analysis_lib — standalone SQL analysis library for ContraGate.

ZERO dependencies on other ContraGate modules.
No LangGraph. No MCP. No LLM calls.
Pure PostgreSQL + psycopg2.

Public API (Phase 1):
  connection.py       — ConnectionConfig, get_production_conn, get_staging_conn
  explain_parser.py   — ExplainResult, parse_explain_json, run_explain
  row_estimator.py    — RowEstimate, estimate_rows, update_confidence
  trigger_detector.py — TriggerInfo, TriggerAnalysis, detect_triggers
  cascade_tracer.py   — CascadeLevel, CascadeGraph, trace_cascades
  reversibility_rules.py — ReversibilityClass, ReversibilityResult, classify_reversibility
"""

from .connection import ConnectionConfig, get_production_conn, get_staging_conn
from .explain_parser import ExplainResult, parse_explain_json, run_explain
from .row_estimator import RowEstimate, estimate_rows, update_confidence
from .trigger_detector import TriggerInfo, TriggerAnalysis, detect_triggers
from .cascade_tracer import CascadeLevel, CascadeGraph, trace_cascades
from .reversibility_rules import (
    ReversibilityClass,
    ReversibilityResult,
    classify_reversibility,
)

__all__ = [
    "ConnectionConfig",
    "get_production_conn",
    "get_staging_conn",
    "ExplainResult",
    "parse_explain_json",
    "run_explain",
    "RowEstimate",
    "estimate_rows",
    "update_confidence",
    "TriggerInfo",
    "TriggerAnalysis",
    "detect_triggers",
    "CascadeLevel",
    "CascadeGraph",
    "trace_cascades",
    "ReversibilityClass",
    "ReversibilityResult",
    "classify_reversibility",
]
