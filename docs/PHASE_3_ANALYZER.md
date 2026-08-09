# Phase 3 Analyzer Agent — Implementation Report

**Date:** 2026-08-08  
**Verdict:** PHASE 3 COMPLETE — READY FOR PHASE 4

---

## 1. Deliverables

| File | Status | Description |
|------|--------|-------------|
| `agents/base_agent.py` | NEW | BaseAgent with audit-logging tool call lifecycle |
| `agents/mcp_client.py` | NEW | Agent-level MCP client (re-exports orchestrator's) |
| `agents/analyzer/prompts.py` | IMPLEMENTED | Structured output prompts (tool_use schema) |
| `agents/analyzer/tools.py` | IMPLEMENTED | 8 tool functions (pure — no MCP, no LLM) |
| `agents/analyzer/agent.py` | IMPLEMENTED | Full Analyzer Agent orchestration |
| `orchestrator/states/analysis.py` | UPDATED | Delegates to real AnalyzerAgent |
| `mcp_servers/audit_logger/checksum.py` | NEW | Extracted SHA-256 checksum helpers |
| `mcp_servers/audit_logger/server.py` | UPDATED | Imports from checksum.py |
| `mcp_servers/policy_store/rule_engine.py` | NEW | Extracted deterministic rule engine |
| `mcp_servers/policy_store/server.py` | UPDATED | Uses rule_engine.evaluate_operation() |
| `tests/unit/__init__.py` | NEW | Unit test directory |
| `tests/unit/test_analyzer_agent.py` | NEW | 77 unit tests |
| `tests/unit/test_policy_rule_engine.py` | NEW | 25 unit tests for rule engine |
| `tests/integration/test_analyzer_agent.py` | NEW | 33 integration tests |

---

## 2. Architecture

### BaseAgent Audit Lifecycle

Every MCP tool call made by AnalyzerAgent goes through `BaseAgent.call_tool()`:

```
call_tool(server, tool, args)
  → hash_payload(args)           # SHA-256 of serialized input
  → dispatch to MCP server       # actual HTTP call
  → hash_payload(result)         # SHA-256 of serialized output
  → audit_logger.log_tool_call() # fire-and-forget (non-blocking)
  → return result
```

Audit log failure is non-fatal — the pipeline continues even if `audit-logger` is unreachable.

### AnalyzerAgent Flow (10 steps)

```
analyze(contract)
  1. Selective re-analysis guard  — skip if no stale analysis fields
  2. classify_operation_type()    — pure SQL parsing
  3. parse_sql_intent()           — extract (table, condition)
  4. _count_affected_rows()       — postgres-reader: estimate_row_count
  5. _detect_triggers()           — postgres-reader: list_triggers
  6. _trace_fk_graph()            — postgres-reader: get_fk_graph (DELETE/UPDATE only)
  7. _check_soft_delete()         — postgres-reader: check_soft_delete (DELETE/UPDATE only)
  8. classify_reversibility()     — sql_analysis_lib (deterministic, no LLM)
  9. _get_intent_summary()        — Anthropic API (structured output, fallback on error)
 10. add_provenance()             — ANALYZER_AGENT entry with llm_involved=True
     log_agent_output()          — audit log of final contract hash
```

### Structured LLM Output

The only LLM call uses Anthropic tool_use to force typed JSON:

```python
response = await client.messages.create(
    model="claude-sonnet-5",
    tools=[INTENT_SUMMARY_TOOL],
    tool_choice={"type": "tool", "name": "produce_intent_summary"},
    messages=[{"role": "user", "content": prompt}],
)
```

The model receives pre-computed structured fields, not raw SQL for analysis.
Raw SQL is wrapped in `<untrusted_input>` tags and treated as data.

### Policy Rule Engine Extraction

`mcp_servers/policy_store/rule_engine.py` contains `evaluate_operation()` as a pure function. The MCP server fetches DB data and calls it. Unit tests call it directly with no database.

---

## 3. Security Invariants Verification

| Invariant (CLAUDE.md §22) | Status | Evidence |
|---------------------------|--------|---------|
| No LLM reversibility classification | ✓ ENFORCED | `classify_reversibility()` from `sql_analysis_lib` — deterministic |
| No LLM rollback SQL | ✓ ENFORCED | `contract.automated_recovery_sql = None` always (ADR-008) |
| No LLM risk tier | ✓ ENFORCED | Risk tier is Contract Agent's domain — not set in ANALYZER |
| Raw SQL wrapped as untrusted input | ✓ ENFORCED | `build_intent_prompt()` wraps in `<untrusted_input>` tags |
| Every MCP call hashed + logged | ✓ ENFORCED | `BaseAgent.call_tool()` lifecycle |
| All DB access via MCP servers | ✓ ENFORCED | No direct psycopg2 connections in agent code |
| Provenance on every agent write | ✓ ENFORCED | `contract.add_provenance(agent="ANALYZER_AGENT", ...)` |
| No LLM policy decisions | ✓ ENFORCED | `evaluate_operation()` is pure deterministic function |
| Audit log failure non-fatal | ✓ ENFORCED | `try/except` in `_log_tool_call` and `log_agent_output` |
| Fallback when LLM unavailable | ✓ ENFORCED | `_fallback_intent_summary()` rule-based fallback |

---

## 4. Test Results

```
312 passed, 42 skipped, 0 failed
```

| Suite | Tests | Notes |
|-------|-------|-------|
| `tests/unit/test_analyzer_agent.py` | 77 | Pure function + mocked agent tests |
| `tests/unit/test_policy_rule_engine.py` | 25 | All 8 policy rules |
| `tests/integration/test_analyzer_agent.py` | 33 | Full analyze() flows, mocked MCP |
| `tests/integration/test_analysis_state.py` | 18 | Existing tests — all pass (private helpers preserved) |
| All Phase 1 + 2 tests | Unchanged | No regressions |

**42 skipped tests** are all PostgreSQL-dependent tests that require a live database — same as Phase 2 baseline. Not a Phase 3 regression.

---

## 5. Demo Scenario Verification

**SQL:** `DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'`

Test: `test_full_analysis_permanent_no_soft_delete`

| Field | Expected | Actual |
|-------|----------|--------|
| `operation_type` | `DELETE` | `DELETE` ✓ |
| `primary_table` | `"users"` | `"users"` ✓ |
| `condition` | contains `last_active` | `"last_active < NOW() - INTERVAL '2 years'"` ✓ |
| `estimated_primary_rows` | 4,200 (mocked) | 4,200 ✓ |
| `cascade[0].table` | `"orders"` | `"orders"` ✓ |
| `cascade[1].table` | `"invoices"` | `"invoices"` ✓ |
| `reversibility` | `PERMANENT` | `PERMANENT` ✓ |
| `automated_recovery_sql` | `None` | `None` ✓ (ADR-008) |
| `intent_summary` | non-empty | ✓ |
| `workflow_provenance` | ANALYZER_AGENT entry | ✓ |

---

## 6. Known Limitations (Phase 3)

1. **Anthropic model hardcoded via env var** — defaults to `claude-sonnet-5`. Override with `ANTHROPIC_MODEL` env var.

2. **PITR confirmation not implemented** — `pitr_confirmed=False` always in MVP. DELETE/UPDATE on tables without soft-delete columns will always be PERMANENT. Phase 9 adds PITR status checks.

3. **Audit log is fire-and-forget** — if `audit-logger` MCP server is down during heavy load, tool call records may be lost. Phase 8 adds a write-ahead buffer.

4. **`asyncio.get_event_loop().create_task()` in BaseAgent** — may emit a DeprecationWarning in Python 3.12+ when there is no current event loop. In production (asyncio context) this always works correctly.

5. **`estimate_row_count` passes `condition or None`** — for SQL with complex subqueries in the WHERE clause (e.g., nested CTE), `sql_analysis_lib.row_estimator` may fall back to `reltuples` stats. Documented in `row_estimator.py`.

---

## 7. Phase 4 Boundary

Phase 3 ends at the ANALYSIS → CONTEXT_AND_SIM boundary.

**Phase 4 implements:**
- Context Agent: three-stage retrieval (semantic search → Jaccard filter → outcome reranking)
- Simulation Agent: BEGIN/execute/capture/ROLLBACK sandbox cycle
- Memory store: pgvector embeddings (replaces Phase 2 ILIKE fallback in `semantic_search`)

**Phase 4 must NOT modify:**
- `agents/analyzer/agent.py` — Analyzer Agent is complete
- `agents/base_agent.py` — BaseAgent infrastructure is shared
- `orchestrator/states/analysis.py` — ANALYSIS state is complete
- `mcp_servers/policy_store/rule_engine.py` — Policy rules are complete

---

*Phase 3 complete. All 312 tests pass. No secrets in any file.*
