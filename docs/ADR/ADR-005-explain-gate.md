# ADR-005: Read Impact Gate Uses EXPLAIN Before Routing

**Status:** Accepted  
**Date:** 2026-08-08  
**Source:** ContraGate MVP.pdf — Design Decision Reference

---

## Context

Read operations are not automatically safe. A SELECT that scans a PII-tagged table or exports millions
of rows requires review. But the system faces a circular dependency:

- Fast-path routing (auto-execute without analysis) requires a risk score
- Computing a risk score for a read requires running the full Analyzer Agent
- Running the full Analyzer Agent defeats the purpose of the fast path

The system needs a way to distinguish trivially safe reads from potentially harmful ones without running
the full pipeline.

---

## Decision

ContraGate runs `EXPLAIN (FORMAT JSON)` against the `postgres-reader` MCP server for **every incoming
operation** (including reads) before any routing decision. The EXPLAIN output provides a preliminary
risk signal sufficient for routing without the full pipeline.

---

## Rationale

`EXPLAIN` (without `ANALYZE`) estimates row count and cost without executing the query. It:
- Runs in under 10 milliseconds
- Requires only SELECT permissions (available on `postgres-reader`)
- Provides: estimated row count, estimated cost, scan type (sequential vs. index), partition involvement

These four values are sufficient to compute a preliminary risk score for routing:
- If EXPLAIN cost > 50,000 cost units → standard review minimum
- If any table in the query is tagged PII in the policy store → standard review minimum
- Otherwise → fast path auto-execute candidate

**CRITICAL timing:** EXPLAIN runs at the start of RISK_GATE, before the Policy Gate, before any agent.
It does NOT run after the full analysis pipeline. Its purpose is to route without analysis, so it must
run before analysis.

---

## Thresholds

| Threshold | Value | Justification |
|-----------|-------|--------------|
| EXPLAIN cost threshold (general) | 50,000 cost units | Calibrated to allow simple index lookups and small table scans through the fast path |
| EXPLAIN cost threshold (PII reads) | 100,000 cost units | PII reads trigger standard review at higher threshold per policy |

Both thresholds are configurable via environment variables:
- `CONTRAGATE_EXPLAIN_THRESHOLD` (default: 50,000)

---

## Consequences

- `proxy/risk_gate.py` must run EXPLAIN before the Policy Gate
- `sql_analysis_lib/explain_parser.py` parses the EXPLAIN JSON output
- The `postgres-reader` MCP server must support EXPLAIN tool calls
- EXPLAIN is not a substitute for the full analysis — it is only a routing pre-filter
- Operations that pass the EXPLAIN threshold still go through the full Policy Gate before auto-execution
