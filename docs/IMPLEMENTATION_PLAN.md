# ContraGate Implementation Plan

> **Authoritative source:** CLAUDE.md §30 (Phase-by-Phase Implementation Order)  
> This document expands each phase with explicit deliverables, dependencies, and exit criteria.

---

## Phase Dependency Graph

```
Phase 0: Architecture + Repository Foundation
    │
    └─► Phase 1: sql_analysis_lib (5 modules + 30 fixtures + all tests passing)
            │
            └─► Phase 2: Docker + MCP Servers + LangGraph Skeleton + Async Protocol
                    │
                    └─► Phase 3: Analyzer Agent
                            │
                            └─► Phase 4: Context + Simulation Agent
                                    │
                                    └─► Phase 5: Contract Agent
                                            │
                                            └─► Phase 6: Full Orchestration (all states, guards, timeouts)
                                                    │
                                                    └─► Phase 7: UI + Approval
                                                            │
                                                            └─► Phase 8: Audit + Feedback Loop
                                                                    │
                                                                    └─► Phase 9: Integration + Deployment
                                                                            │
                                                                            └─► Phase 10: Hardening + Verification
```

---

## Phase 0 — Architecture + Repository Foundation

**Entry criterion:** Empty repository (only PDFs present)  
**Exit criterion:** CLAUDE.md complete, all scaffold stubs created, .gitignore correct, no secrets committed

### Deliverables

- [x] `CLAUDE.md` — complete engineering contract
- [x] `docs/ADR/ADR-001` through `ADR-008`
- [x] `docs/IMPLEMENTATION_PLAN.md`
- [ ] Complete directory structure with stub files
- [ ] `.gitignore`
- [ ] `.env.example`
- [ ] `docker-compose.yml` (structure only)
- [ ] `docker-compose.prod.yml` (structure only)
- [ ] `railway.json`
- [ ] `README.md`

### What Phase 0 Does NOT Include

- No real implementation of agents, MCP servers, or orchestration
- No real SQL analysis code
- No database connections
- No Docker containers that actually work end-to-end

---

## Phase 1 — sql_analysis_lib

**Entry criterion:** Phase 0 complete and reviewed  
**Exit criterion:** 100% of 30 fixture tests pass against a real PostgreSQL 16 instance

### Deliverables

#### Modules (implement in this order — each depends on the previous)

1. **`sql_analysis_lib/explain_parser.py`**
   - Parse `EXPLAIN (FORMAT JSON)` output
   - Extract: estimated rows, actual rows (if ANALYZE), total cost, scan type, partition involvement
   - Input: raw JSON string from PostgreSQL EXPLAIN
   - Output: typed `ExplainResult` Pydantic model
   - No database connection required — pure parsing
   - Test: `test_explain_parser.py` against fixture JSON strings

2. **`sql_analysis_lib/row_estimator.py`**
   - Query `pg_stat_user_tables` for fast approximate counts
   - For WHERE conditions: call EXPLAIN and parse via explain_parser
   - Return `RowEstimate` with value and confidence_score
   - Confidence: 0.8 if stats updated within 24h, graduated lower for stale stats
   - Requires PostgreSQL connection (psycopg2)
   - Test: `test_estimator.py` against 30 fixtures

3. **`sql_analysis_lib/trigger_detector.py`**
   - Query `pg_trigger`, `pg_proc`, `pg_extension`
   - Classify triggers by non-transactional extension invocation
   - Non-transactional list: pg_net, dblink + configurable list
   - Return `TriggerAnalysis` with list of triggers and their classifications
   - Test: `test_triggers.py` against 30 fixtures

4. **`sql_analysis_lib/cascade_tracer.py`**
   - Query `information_schema.referential_constraints` and `information_schema.key_column_usage`
   - Build FK dependency graph recursively up to configurable depth (default: 5 levels)
   - Given table + WHERE condition, estimate cascade row counts using parent-table selectivity
   - Return `CascadeGraph` with ordered list of dependent tables and estimated row counts
   - Test: `test_cascade.py` against 30 fixtures

5. **`sql_analysis_lib/reversibility_rules.py`**
   - Pure function: takes operation_type, table_metadata, trigger_analysis, policy_state as input
   - Returns one of: REVERSIBLE_AUTOMATED, REVERSIBLE_PITR, PARTIAL, PERMANENT
   - Priority order (hardcoded — not configurable):
     1. DDL → PERMANENT
     2. Non-transactional external effects → PERMANENT
     3. DELETE/UPDATE without soft-delete and without PITR → PERMANENT
     4. Snapshot feasible → REVERSIBLE_AUTOMATED
     5. PITR available → REVERSIBLE_PITR
     6. Mixed → PARTIAL
   - No database connection. No LLM call. No external dependencies.
   - Test: `test_reversibility.py` against all 30 fixtures

#### Fixture Suite

30 SQL operation scenarios in `tests/fixtures/`. Each fixture is a JSON file:
```json
{
  "id": "fixture_001",
  "description": "Simple SELECT on non-PII table",
  "sql": "SELECT id, name FROM products WHERE category = 'electronics'",
  "schema_context": { ... },
  "expected_reversibility": "REVERSIBLE_AUTOMATED",
  "expected_operation_type": "SELECT",
  "expected_cascade_tables": [],
  "expected_external_triggers": [],
  "expected_explain_cost_range": [0, 1000]
}
```

### Dependencies

- PostgreSQL 16 with pgvector installed (for integration tests)
- psycopg2-binary Python package
- pytest + pytest-postgresql

---

## Phase 2 — Docker + MCP Servers + LangGraph Skeleton + Async Protocol

**Entry criterion:** Phase 1 complete (all 30 fixture tests pass)  
**Exit criterion:** All 7 containers run, health checks pass, smoke test passes

### Deliverables

1. **`docker-compose.yml`** — all 7 containers with health checks and dependency ordering
2. **`seed/` SQL files** — all 5 seed files fully implemented
3. **`mcp_servers/postgres_reader/`** — real SELECT-only MCP server against production schema
4. **`mcp_servers/transaction_sandbox/`** — real writable staging MCP server with sandbox enforcement
5. **`mcp_servers/memory_store/`** — real pgvector read/write server, tenant-scoped
6. **`mcp_servers/audit_logger/`** — real append-only MCP server with checksums
7. **`mcp_servers/policy_store/`** — real read-only (from agent pipeline) MCP server
8. **`mcp_servers/notifier/`** — local mock (terminal print + /dev/approve, /dev/reject endpoints)
9. **`orchestrator/graph.py`** — LangGraph skeleton with all 8 states, guard conditions, transition logic (no real agent calls yet — stub agents)
10. **`orchestrator/handoff_schema.py`** — complete Pydantic v2 HandoffContract schema with validation
11. **`proxy/main.py`** + **`proxy/async_protocol.py`** — MCP proxy with async pending protocol, polling endpoints, SSE streams
12. **`proxy/risk_gate.py`** — Read Impact Gate (EXPLAIN) + Policy Gate (rule evaluation)

**Smoke test:** A hardcoded tool call flows from proxy interception → async pending response → stub orchestrator → mock human approval → execution relay → polling response. No real agents yet.

---

## Phase 3 — Analyzer Agent

**Entry criterion:** Phase 2 complete  
**Exit criterion:** E2E test with real Analyzer Agent and stubbed Context+Sim passes

### Deliverables

1. **`agents/analyzer/tools.py`** — all 8 tools as thin wrappers over sql_analysis_lib
2. **`agents/analyzer/prompts.py`** — structured output prompts; all plan descriptions wrapped in `<untrusted_input>` tags
3. **`agents/analyzer/agent.py`** — Analyzer Agent orchestration; LLM produces typed JSON only

**Key requirement:** prompt injection boundary tagging must be implemented at intake — all raw SQL and
external plan descriptions are wrapped in `<untrusted_input>...</untrusted_input>` before any LLM call.

---

## Phase 4 — Context and Simulation Agent

**Entry criterion:** Phase 3 complete  
**Exit criterion:** Retrieval verified with seeded data; simulation verified with real SQL producing real row diffs

### Deliverables

1. **`agents/context_sim/retrieval.py`** — three-stage retrieval (semantic → structural filter → outcome reranking)
2. **`agents/context_sim/sandbox.py`** — transaction sandbox execution with retry logic
3. **`agents/context_sim/agent.py`** — orchestration; retrieval and simulation run in parallel

**Key requirements:**
- Seed `seed/historical_operations.sql` into memory store before testing
- Verify cg_1847 surfaces as top result for "DELETE FROM users WHERE last_active" query
- Verify sandbox captures real row counts on the staging database
- Verify parallel execution of retrieval and simulation within the agent

---

## Phase 5 — Contract Agent

**Entry criterion:** Phase 4 complete  
**Exit criterion:** Full three-agent pipeline tested end-to-end against real SQL operations

### Deliverables

1. **`agents/contract/risk_rules.py`** — deterministic risk tier rules (no LLM)
2. **`agents/contract/contract_builder.py`** — contract section assembly
3. **`agents/contract/contract_schema.py`** — approval contract JSON schema
4. **`agents/contract/agent.py`** — Contract Agent orchestration; LLM call for NL summarization only

**Key requirement:** Risk tier output must be identical for identical inputs across all invocations.
Write a test that calls the Contract Agent twice with the same HandoffContract and verifies the
risk_tier field is identical.

---

## Phase 6 — Full Orchestration

**Entry criterion:** Phase 5 complete  
**Exit criterion:** All workflow tests pass (normal flow, auto-execute, timeout, retry, modify, rejection)

### Deliverables

1. **All 8 LangGraph states fully implemented** — not stubs
2. **`orchestrator/guards.py`** — all guard conditions with tests
3. **`orchestrator/retry.py`** — exponential backoff for sandbox retries
4. **Selective re-analysis on MODIFY** — only stale states re-run; CONTRACT reuses cached outputs

**Key requirement:** After a MODIFY decision, verify in the LangGraph trace that ANALYSIS and
CONTEXT_AND_SIM re-run but CONTRACT reuses the cached unchanged sections.

---

## Phase 7 — UI + Approval

**Entry criterion:** Phase 6 complete  
**Exit criterion:** All four UI interaction paths tested (approve, reject, modify, request prerequisite)

### Deliverables

1. **`ui/src/pages/ApprovalQueue.jsx`** — pending approvals sorted by time remaining
2. **`ui/src/pages/ContractView.jsx`** — full four-section contract rendering
3. **`ui/src/pages/AuditLog.jsx`** — completed operations with accuracy deltas
4. **`ui/src/components/PermanentGate.jsx`** — acknowledgement checkbox; Approve disabled until checked for PERMANENT ops
5. **`ui/src/components/DecisionPanel.jsx`** — reason field enforced (10 char minimum); Approve/Reject disabled until satisfied
6. **`ui/src/hooks/useApprovalPolling.js`** — SSE subscription + polling fallback

---

## Phase 8 — Audit + Feedback Loop

**Entry criterion:** Phase 7 complete  
**Exit criterion:** Feedback loop visible in audit log UI; confidence adjustments verified

### Deliverables

1. **`mcp_servers/audit_logger/`** — fully implemented with row-level SHA-256 checksums
2. **Post-execution verification job** — queries actual row counts, computes accuracy delta
3. **Confidence score adjustment** — underestimate: -0.05, overestimate: -0.02
4. **`ui/src/pages/AuditLog.jsx`** — accuracy deltas visible per operation

**Demo verification:** After scenario 2 executes, the users table confidence score update must be
visible in the audit log before scenario 3 begins.

---

## Phase 9 — Integration + Deployment

**Entry criterion:** Phase 8 complete  
**Exit criterion:** All four demo scenarios produce expected outputs on Railway

### Deliverables

1. **`docker-compose.prod.yml`** — Railway overrides
2. **`railway.json`** — Railway deployment configuration
3. **`mcp_servers/notifier/`** — Slack integration (replaces local mock)
4. **Railway deployment** — two PostgreSQL instances + four services running
5. **Seed scripts run against Railway databases**
6. **Online demo database** — 50,000 users, 180,000 orders, 430,000 invoices, 2,100,000 notifications

**Demo scenario verification:** All four scenarios must produce expected outputs consistently.

---

## Phase 10 — Hardening + Final Verification

**Entry criterion:** Phase 9 complete  
**Exit criterion:** Three-minute screen recording produced; README complete; secrets audit clean

### Deliverables

1. **Secrets audit** — verify no secrets in git history, .env.example contains only key names
2. **Known limitations section** — written honestly in README
3. **Design decisions section** — five interview questions answered in README
4. **Three-minute screen recording** — covers all four demo scenarios
5. **Repository cleanup** — no __pycache__, no node_modules, no .pyc in history

---

## Dependency Matrix

| Phase | Depends On |
|-------|-----------|
| Phase 1 | Phase 0 |
| Phase 2 | Phase 1 (sql_analysis_lib tests passing) |
| Phase 3 | Phase 2 (MCP servers running, LangGraph skeleton) |
| Phase 4 | Phase 3 (Analyzer Agent complete) |
| Phase 5 | Phase 4 (Context+Sim Agent complete) |
| Phase 6 | Phase 5 (all three agents complete) |
| Phase 7 | Phase 6 (full orchestration working) |
| Phase 8 | Phase 7 (UI interactions working) |
| Phase 9 | Phase 8 (audit + feedback loop complete) |
| Phase 10 | Phase 9 (Railway deployment running) |

No phase may begin before the previous phase's exit criterion is confirmed.
