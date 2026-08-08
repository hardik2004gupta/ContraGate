# CLAUDE.md — ContraGate Engineering Contract

> **This document is the authoritative engineering contract for all ContraGate implementation phases.**
> A fresh Claude Code session reading only this file plus the repository must understand exactly how ContraGate
> must be built. Do not deviate from this specification. Do not add features not described here.

---

## 1. Project Identity

| Field | Value |
|-------|-------|
| **Name** | ContraGate |
| **Tagline** | Know the consequences before the action executes. |
| **One-Line** | ContraGate is an agentic workflows layer that transparently intercepts MCP tool-calling agents, runs a three-agent pre-execution audit covering blast radius analysis, reversibility classification, three-stage memory retrieval, and transaction-sandboxed simulation, and presents humans with risk-tiered consequence contracts before any action executes against production. |

**Why ContraGate:** "Contra" = counterfactual — the system computes what will happen before it happens.
"Gate" = checkpoint between agent intent and production execution.

---

## 2. Source-of-Truth Documents

The following two PDF files live at the repository root and are the canonical specification:

1. `ContraGate_Complete_Architecture_and_Repository_Plan.pdf`
2. `ContraGate MVP.pdf`

**Resolution rule when PDFs conflict:** the more specific, operationally detailed requirement wins. Every conflict
must be documented with a decision record. The resolved decision is binding.

---

## 3. Product Purpose

Every major agent framework (LangChain, LangGraph, AutoGen, CrewAI) includes a human approval step. Without
exception, they show the human what the agent wants to do — not what will actually happen. ContraGate builds
the missing layer: the consequence-aware gate between agent capability and production execution.

**Three failure modes ContraGate solves:**
1. **Uninformed approval** — humans approve with no visibility into row counts, cascades, or external effects.
2. **Approval fatigue** — thin context trains humans to approve reflexively.
3. **Institutional amnesia** — every approval is made in isolation; historical outcomes are never surfaced.

---

## 4. MVP Scope

- **In scope:** PostgreSQL SQL-modifying operations via MCP tool calls.
- **Architecture is designed for extension** to other tool domains without redesign, but only PostgreSQL is
  implemented in the MVP.
- **Single-tenant MVP** — multi-tenancy infrastructure exists but only `demo_tenant` is used.
- **Single approver** — the policy schema includes `dual_approval_required` for future use; not implemented.
- **No OIDC** — API key authentication for approvers only.

---

## 5. Non-Goals (Explicit)

These are NOT built in the MVP:

- Other database types (MySQL, MongoDB, etc.)
- Cross-domain consequence propagation (CRM, billing pipelines)
- Dual-approval workflows
- OIDC / role-based authorization
- Physical read replica sandbox (requires a writable staging instance — see ADR-002)
- Full simulation of non-transactional extensions (pg_net, dblink calls are detected and flagged, not simulated)
- Propagation Agent for cross-system consequence tracing

---

## 6. Architecture Overview

```
External Agent (MCP Client)
        │  JSON-RPC tool call
        ▼
ContraGate MCP Proxy  (port 8000)
        │  intercept + async pending response
        ▼
Risk Gate
  ├─ Read Impact Gate  (EXPLAIN FORMAT JSON — always first)
  └─ Policy Gate  (deterministic rule engine — before agent pipeline)
        │  if auto-execute → skip to AUDIT
        │  if review required ↓
        ▼
LangGraph Orchestrator
        │
        ├─ ANALYSIS  (Analyzer Agent)
        │
        ├─ CONTEXT_AND_SIM  (Context + Simulation Agent — parallel retrieval + sandbox)
        │
        └─ CONTRACT  (Contract Agent)
                │
                ▼
        Human Review  (React UI / Slack notification)
                │
        ┌───────┼────────────────┐
        ▼       ▼                ▼
     APPROVE  REJECT       MODIFY (→ selective re-analysis)
        │       │
        ▼       ▼
   EXECUTION  AUDIT
        │
        ▼
      AUDIT + Feedback Loop
```

---

## 7. Repository Structure

This is the canonical structure. Every file listed here will exist. Scaffold stubs are created in Phase 0;
real implementations follow the phase plan.

```
contragate/
├── CLAUDE.md                          # This file — engineering contract
├── README.md                          # Public-facing documentation
├── .env.example                       # Required environment variable template
├── .gitignore
├── docker-compose.yml                 # Seven-container local dev environment
├── docker-compose.prod.yml            # Railway production overrides
├── railway.json                       # Railway deployment configuration
│
├── proxy/
│   ├── main.py                        # MCP proxy entry point, port 8000
│   ├── interceptor.py                 # Tool call interception and routing
│   ├── async_protocol.py              # Pending task payload and polling endpoints
│   ├── risk_gate.py                   # Read Impact Gate and Policy Gate
│   └── toolset_config.yaml            # Known PostgreSQL MCP tool → schema mapping
│
├── orchestrator/
│   ├── graph.py                       # LangGraph state machine definition
│   ├── states/
│   │   ├── intake.py
│   │   ├── risk_gate.py
│   │   ├── analysis.py
│   │   ├── context_sim.py
│   │   ├── contract.py
│   │   ├── human_review.py
│   │   ├── execution.py
│   │   └── audit.py
│   ├── guards.py                      # Guard condition implementations
│   ├── retry.py                       # Retry logic and exponential backoff
│   └── handoff_schema.py              # Typed handoff contract schema and validation
│
├── agents/
│   ├── analyzer/
│   │   ├── agent.py                   # Analyzer Agent orchestration
│   │   ├── prompts.py                 # Structured output prompts (JSON schema enforced)
│   │   └── tools.py                   # Tool implementations wrapping sql_analysis_lib
│   ├── context_sim/
│   │   ├── agent.py                   # Context and Simulation Agent orchestration
│   │   ├── retrieval.py               # Three-stage retrieval implementation
│   │   └── sandbox.py                 # Transaction sandbox execution logic
│   └── contract/
│       ├── agent.py                   # Contract Agent orchestration
│       ├── risk_rules.py              # Deterministic risk tier classification
│       ├── contract_builder.py        # Contract section assembly
│       └── contract_schema.py         # Approval contract JSON schema
│
├── mcp_servers/
│   ├── postgres_reader/
│   │   ├── server.py                  # MCP server, SELECT-only role
│   │   └── schema.json                # Tool definitions
│   ├── transaction_sandbox/
│   │   ├── server.py                  # MCP server, staging write role, no egress
│   │   └── schema.json
│   ├── memory_store/
│   │   ├── server.py                  # pgvector read/write, tenant-scoped
│   │   └── schema.json
│   ├── audit_logger/
│   │   ├── server.py                  # Append-only, checksum on write
│   │   └── schema.json
│   ├── policy_store/
│   │   ├── server.py                  # Read-only from agent pipeline
│   │   └── schema.json
│   └── notifier/
│       ├── server.py                  # Slack MCP integration (mock for local dev)
│       └── schema.json
│
├── sql_analysis_lib/                  # PHASE 1 — standalone prerequisite library
│   ├── __init__.py
│   ├── cascade_tracer.py              # FK cascade graph from information_schema
│   ├── row_estimator.py               # Row count from pg_stat + EXPLAIN
│   ├── trigger_detector.py            # Trigger and non-transactional extension detection
│   ├── reversibility_rules.py         # Deterministic reversibility classification
│   ├── explain_parser.py              # EXPLAIN JSON output parsing
│   └── tests/
│       ├── __init__.py
│       ├── conftest.py                # PostgreSQL test fixture setup
│       ├── test_cascade.py
│       ├── test_estimator.py
│       ├── test_triggers.py
│       ├── test_reversibility.py
│       ├── test_explain_parser.py
│       └── fixtures/                  # 30 SQL operation test scenarios
│           ├── README.md              # Documents all 30 scenarios
│           └── *.json                 # Individual fixture files
│
├── ui/
│   ├── src/
│   │   ├── App.jsx
│   │   ├── pages/
│   │   │   ├── ApprovalQueue.jsx      # Pending approvals sorted by timeout
│   │   │   ├── ContractView.jsx       # Full consequence contract rendering
│   │   │   └── AuditLog.jsx           # Completed operations + accuracy deltas
│   │   ├── components/
│   │   │   ├── ContractSection.jsx
│   │   │   ├── DecisionPanel.jsx      # Decision options and reason capture
│   │   │   ├── PermanentGate.jsx      # Acknowledgement checkbox for PERMANENT ops
│   │   │   ├── HistoricalCard.jsx     # Historical precedent display
│   │   │   └── SystemFlags.jsx        # Policy violations and risk tags
│   │   └── hooks/
│   │       └── useApprovalPolling.js  # SSE and polling integration
│   └── package.json
│
├── seed/
│   ├── demo_schema.sql                # E-commerce schema (users, orders, invoices, notifications)
│   ├── historical_operations.sql      # 20 seeded operations (5 rejected, 5 rolled back, etc.)
│   ├── staging_schema.sql             # Staging database initialization
│   ├── default_policies.sql           # 8 default policy rules
│   └── sandbox_trigger.sql            # sandbox_trigger_log table and sandbox-aware triggers
│
└── docs/
    ├── ADR/
    │   ├── ADR-001-three-agents.md
    │   ├── ADR-002-transaction-sandbox.md
    │   ├── ADR-003-deterministic-risk.md
    │   ├── ADR-004-async-approval.md
    │   ├── ADR-005-explain-gate.md
    │   ├── ADR-006-sql-analysis-prerequisite.md
    │   ├── ADR-007-mcp-only-external-access.md
    │   └── ADR-008-no-llm-rollback.md
    └── IMPLEMENTATION_PLAN.md
```

---

## 8. Three-Agent Responsibilities

### Agent 1: Analyzer Agent

**Runs:** First in the pipeline (ANALYSIS state).

**Responsibility:** Convert the intercepted tool call into a structured consequence map covering blast radius,
reversibility, and external side effects.

**Tools (thin wrappers over `sql_analysis_lib`):**

| Tool | Purpose |
|------|---------|
| `parse_sql_intent` | Extract operation type, tables, conditions from raw SQL |
| `validate_schema` | Confirm target tables and columns exist in production schema |
| `count_affected_rows` | Estimate affected rows using pg_stat_user_tables statistics |
| `trace_foreign_keys` | Compute full FK cascade graph using information_schema |
| `list_cascade_tables` | Return ordered list of dependent tables with estimated row counts |
| `estimate_api_fanout` | Estimate volume of external API calls from webhook triggers |
| `classify_operation_type` | Classify as DDL / bulk-write / selective-write / read |
| `detect_trigger_side_effects` | Identify triggers invoking non-transactional extensions |

**LLM usage:** Structured output prompting exclusively — the model produces typed JSON, never free text.
All incoming plan descriptions are wrapped in `<untrusted_input>` boundary tags before LLM processing.

**CRITICAL INVARIANT:** Risk classification is NEVER left to LLM discretion. LLM-generated rollback SQL
is explicitly prohibited.

---

### Agent 2: Context and Simulation Agent

**Runs:** Second (CONTEXT_AND_SIM state). Retrieval and simulation run **in parallel** within this state.

**Responsibility:** Retrieve historically similar operations from memory AND simulate the proposed operation
against real staging data.

**Retrieval tools:**

| Tool | Purpose |
|------|---------|
| `semantic_search_memory` | Cosine similarity search over pgvector embeddings of past intents |
| `filter_by_table_overlap` | Filter candidates by Jaccard similarity of affected table sets |
| `rerank_by_outcome_severity` | Float rejected and rolled-back operations to top |

**Simulation tools:**

| Tool | Purpose |
|------|---------|
| `begin_sandbox_transaction` | Open explicit transaction on staging instance |
| `set_sandbox_mode` | SET LOCAL app.sandbox_mode = 'true' AND statement_timeout = '5000ms' |
| `execute_in_transaction` | Run the proposed SQL inside the open transaction |
| `capture_row_diff` | Record row counts before and after for each affected table |
| `read_trigger_log` | Read sandbox_trigger_log table for external calls that would have fired |
| `rollback_transaction` | ROLLBACK — real data never changes |

**CRITICAL INVARIANT:** Simulation NEVER executes against production. Sandbox uses a writable staging
instance — NEVER a physical read replica (which rejects writes even inside a ROLLBACK-planned transaction).

**Sandbox guarantee:** The `sandbox_mode` flag is read by application-level triggers, which log their
intended external calls to `sandbox_trigger_log` instead of executing them.

**Timeout behavior:** Sandbox timeout → retry twice with exponential backoff → if still unavailable,
continue with simulation marked unavailable (explicitly flagged in contract).

**Memory unavailable:** Continue without historical context, explicitly flagged in contract.

---

### Agent 3: Contract Agent

**Runs:** Third (CONTRACT state).

**Responsibility:** Assemble all agent outputs into a human-readable consequence contract and apply
deterministic risk tier classification.

**Tools:**

| Tool | Purpose |
|------|---------|
| `compile_approval_doc` | Assemble structured contract JSON from handoff contract fields |
| `apply_risk_tier_rules` | Deterministic rule engine returning AUTO / STANDARD / FULL tier |
| `format_consequence_report` | Produce human-readable section text for each contract section |
| `generate_rollback_plan` | Derive rollback procedure from reversibility classification |

**LLM usage:** Natural language summarization ONLY — taking structured fields from the typed handoff
contract and producing readable prose. Risk tier classification is entirely rule-based code, not LLM output.

**CRITICAL INVARIANT:** An LLM must NEVER override a hard policy rule. Risk tier classification must be
deterministic and reproducible.

---

## 9. MCP Server Architecture

**Architectural rule:** All external system access flows through MCP servers. No agent makes arbitrary
direct database connections or API calls. Every tool call is schema-validated, permission-scoped, and
automatically logged.

### `postgres-reader`
- **Permissions:** SELECT only on production database
- **Purpose:** Schema inspection, EXPLAIN queries, row count estimation, foreign key tracing, trigger detection
- **Runs against:** Production database, read-only role
- **NEVER used for:** Writes, simulation, execution

### `transaction-sandbox`
- **Permissions:** Read-write on staging database. **No network egress.**
- **Purpose:** BEGIN/ROLLBACK simulation execution, sandbox_trigger_log reads
- **Runs against:** Writable staging instance — NEVER a physical read replica
- **Enforces on every connection:**
  - `SET LOCAL statement_timeout = '5000ms'`
  - `SET LOCAL app.sandbox_mode = 'true'`
- **CRITICAL:** Network egress from this server is blocked at the infrastructure level

### `memory-store`
- **Permissions:** Read and write to operation memory schema, scoped by tenant identifier
- **Purpose:** pgvector semantic search, historical operation retrieval, post-decision write-back
- **Tenant scoping:** Every query filters by `tenant_id` — no cross-tenant data access is possible

### `audit-logger`
- **Permissions:** Append-only. No UPDATE, no DELETE from normal operations.
- **Purpose:** Tamper-evident record of every tool call, agent output, and human decision
- **Integrity:** Row-level checksums on write. Admin-only DELETE with separate authorization path.

### `policy-store`
- **Permissions:** Read-only from agent pipeline. Write access from policy administration only.
- **Purpose:** Approval policy rules keyed by operation type, table name, and sensitivity tag

### `notifier`
- **Purpose:** Deliver consequence contract summary to human approver via Slack.
  Capture approval decision and stated reason via Slack modal or direct API call.
  Webhook back to LangGraph orchestrator on decision.
- **Local dev:** Mock implementation — prints to terminal, accepts decisions at
  `POST /dev/approve` and `POST /dev/reject`

---

## 10. LangGraph State Machine

### States

```
INTAKE
  │
  ▼
RISK_GATE ──────────────────────────────── AUTO_EXECUTE ──── AUDIT
  │                                         (fast path)
  ▼
ANALYSIS (Analyzer Agent)
  │
  ▼
CONTEXT_AND_SIM (Context + Simulation Agent)
  │  [retrieval and simulation run in parallel within this state]
  │
  ▼
CONTRACT (Contract Agent)
  │
  ▼
HUMAN_REVIEW ──── timeout 30 min ──── auto-REJECT ──── AUDIT
  │
  ├── APPROVE ───────────────────────────────────────────────────┐
  ├── REJECT ──── log reason ──── AUDIT                          │
  └── MODIFY ──── re-enter ANALYSIS with constraints             │
        (stale states re-run, fresh states reuse)                │
                                                                 ▼
                                                           EXECUTION
                                                                 │
                                                                 ▼
                                                              AUDIT
```

### Guard Conditions on Transitions

| Condition | Effect |
|-----------|--------|
| PERMANENT reversibility | Forces FULL_CONTRACT tier regardless of risk score |
| Historical rejection surfaced | Escalates to FULL_CONTRACT regardless of other scores |
| Human sends MODIFY | Only ANALYSIS and CONTEXT_AND_SIM re-run; CONTRACT reuses cached outputs for unchanged sections |
| Sandbox timeout (attempt 1 of 2) | Exponential backoff, retry |
| Sandbox timeout (attempt 2 of 2) | Continue to CONTRACT with simulation marked unavailable |
| Memory store unavailable | Continue without historical context; flag as unavailable |
| No human decision within 30 minutes | AUTO-REJECT; notify calling agent via polling endpoint |

### State Responsibilities

**INTAKE:** Validate incoming tool call manifest. Assign `operation_id`. Record `submitted_by`, `source_type`.
Tag as `<untrusted_input>` if source_type is external.

**RISK_GATE:** Run Read Impact Gate (EXPLAIN), then Policy Gate. Decide: AUTO_EXECUTE or route to full pipeline.
Auto-rejection by policy is logged with the specific rule cited.

**ANALYSIS:** Invoke Analyzer Agent. Write structured blast_radius, reversibility, parsed_plan to handoff contract.

**CONTEXT_AND_SIM:** Invoke Context and Simulation Agent. Run retrieval and simulation in parallel.
Write retrieval_results and simulation to handoff contract.

**CONTRACT:** Invoke Contract Agent. Apply deterministic risk tier rules. Assemble consequence contract.
Write risk_tier, risk_score, contract sections to handoff contract.

**HUMAN_REVIEW:** Send contract to notifier. Start 30-minute countdown. Wait for decision.
Accept: APPROVE, REJECT, MODIFY WITH CONSTRAINTS, REQUEST PREREQUISITE.
Reason is required for APPROVE and REJECT (minimum 10 characters).
For PERMANENT operations: acknowledgement checkbox must be checked before APPROVE activates.

**EXECUTION:** Retrieve original MCP tool-call manifest by approval_id. Verify contract identity has not
changed (stale-state guard). Execute original tool call against real MCP target. Persist result.

**AUDIT:** Write complete audit record. Trigger post-execution feedback loop job.

---

## 11. Typed HandoffContract Schema

All agents communicate through this typed structured JSON object. Inter-agent prose communication is
prohibited. Every agent write must append to `workflow_provenance`.

```json
{
  "operation_id": "cg_7f3a9b12",
  "tenant_id": "demo_tenant",
  "submitted_by": "external-pipeline-agent-v3",
  "source_type": "internal_system",
  "raw_sql": "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'",
  "intent_summary": "",
  "parsed_plan": {
    "operation_type": "DELETE",
    "primary_table": "users",
    "condition": "last_active < NOW() - INTERVAL '2 years'",
    "estimated_primary_rows": 0,
    "confidence_score": 0.0
  },
  "blast_radius": {
    "primary_rows": 0,
    "cascade": [],
    "external_triggers": [],
    "total_rows": 0
  },
  "reversibility": {
    "classification": "",
    "reason": "",
    "automated_recovery_available": false,
    "rollback_sql": null,
    "permanent_components": []
  },
  "retrieval_results": {
    "stage1_candidates": 0,
    "stage2_candidates": 0,
    "top3_historical": [],
    "retrieval_available": true
  },
  "simulation": {
    "executed": false,
    "actual_primary_rows": null,
    "actual_cascade_rows": null,
    "row_diff_available": false,
    "external_calls_logged": [],
    "simulation_available": true,
    "timeout_occurred": false
  },
  "risk_tier": "",
  "risk_score": 0.0,
  "policy_violations": [],
  "prompt_injection_risk": false,
  "approval_state": "PENDING",
  "human_decision": null,
  "decision_reason": "",
  "decision_timestamp": null,
  "workflow_provenance": []
}
```

**Provenance requirement:** Every agent appends to `workflow_provenance` recording which agent wrote which
fields and at what timestamp. This produces a complete lineage of every field in the contract.

The schema is defined in `orchestrator/handoff_schema.py` and validated using Pydantic v2. Any agent
output that fails schema validation raises a hard error — the workflow does not proceed.

---

## 12. SQL Analysis Library

**`sql_analysis_lib`** is a standalone prerequisite implemented in Phase 1 before any agent code.
The Analyzer Agent's tools are thin wrappers over this library.

### Modules

#### `cascade_tracer.py`
Queries `information_schema.referential_constraints` and `information_schema.key_column_usage` to build
a complete FK dependency graph for the target database. Given a table name and WHERE condition, returns
every dependent table with an estimated cascade row count derived from parent-table selectivity.
Handles multi-level cascades recursively up to a configurable depth limit (default: 5 levels).

#### `row_estimator.py`
Queries `pg_stat_user_tables` for `reltuples` and `relpages` to produce fast approximate row counts
without table scans. For operations with a WHERE condition, calls EXPLAIN in estimation mode and parses
the rows estimate from the JSON output. Returns an estimate with a confidence score initialized at 0.8
for tables with statistics updated within 24 hours, lower for stale statistics.

#### `trigger_detector.py`
Queries `pg_trigger`, `pg_proc`, and `pg_extension` to identify all triggers on the target table.
Classifies each trigger by whether it invokes non-transactional extensions — specifically `pg_net`,
`dblink`, and any extension listed in a configurable `non_transactional_extensions` list in the policy
store. Triggers in this list are flagged as generating PERMANENT external side effects.

#### `reversibility_rules.py`
Implements the deterministic reversibility classification rules as a pure function that takes the
operation type, table metadata, trigger classification, and policy store state as inputs and returns
one of four classifications. **No LLM calls. No external dependencies. Fully unit-testable.**

#### `explain_parser.py`
Parses the JSON output of `EXPLAIN (ANALYZE, FORMAT JSON, BUFFERS)` to extract estimated and actual
row counts, total cost estimate, scan type, partition involvement, and trigger execution events.
Used by the Read Impact Gate for pre-routing cost estimation and by the Context and Simulation Agent
for sandbox execution statistics.

### Test Fixtures

The `tests/fixtures/` directory must contain **30 SQL operation scenarios** covering:
- Simple SELECT on non-PII table
- SELECT on PII table
- SELECT with EXPLAIN cost > 50,000
- INSERT single row
- INSERT batch (>1,000 rows)
- UPDATE selective (small row count)
- UPDATE bulk (>10,000 rows)
- UPDATE bulk on PII table
- UPDATE with FK side effects
- DELETE selective (small row count)
- DELETE bulk (>10,000 rows)
- DELETE with single-level cascade
- DELETE with multi-level cascade (3+ levels)
- DELETE with pg_net trigger (external side effect)
- DELETE with dblink trigger
- DELETE without soft-delete column
- DELETE with soft-delete column (deleted_at)
- TRUNCATE table
- DROP TABLE
- ALTER TABLE ADD COLUMN (non-nullable)
- ALTER TABLE DROP COLUMN
- ALTER TABLE RENAME
- CREATE INDEX
- CREATE TABLE
- DELETE on PII table
- UPDATE on external payment table
- Complex multi-table JOIN update
- Nested subquery DELETE
- CTE-based UPDATE
- DDL + DML in transaction

Each fixture is a JSON file containing: `sql`, `schema_context`, `expected_reversibility`,
`expected_operation_type`, `expected_cascade_tables`, `expected_external_triggers`.

---

## 13. Risk Model

### Risk Tiers

| Tier | Condition |
|------|-----------|
| **AUTO_EXECUTE** | Read operation with EXPLAIN cost < 50,000, no PII table involvement, no policy rule triggered |
| **STANDARD_REVIEW** | Reversible write operation, risk score 0.3–0.7, no historical rejection for analogous operation |
| **FULL_CONTRACT** | PERMANENT reversibility OR cascade rows above threshold OR external trigger volume above threshold OR risk score > 0.7 OR uncertainty score > 0.75 OR historical rejection surfaced by retrieval OR prompt injection risk tag present |

### Reversibility Classifications (Deterministic Priority Order)

```
1. DDL operations (DROP, ALTER, TRUNCATE)                        → PERMANENT (always)
2. Non-transactional external effects (pg_net / dblink /
   configured extensions)                                         → PERMANENT for external effects
3. DELETE or UPDATE on tables without soft-delete column
   AND without confirmed PITR snapshot                           → PERMANENT
4. Operations where pre-execution temp table snapshot
   is feasible                                                   → REVERSIBLE_AUTOMATED
5. Operations recoverable via PITR                               → REVERSIBLE_PITR
6. Operations where some components are reversible,
   some are not                                                  → PARTIAL
7. Everything else                                               → classified by operation type,
                                                                    with LLM disambiguation only
                                                                    for genuinely ambiguous edge cases
```

**The four classifications:**
- `REVERSIBLE_AUTOMATED` — mechanical recovery procedure exists
- `REVERSIBLE_PITR` — recoverable via point-in-time recovery
- `PARTIAL` — some components reversible, some permanently lost
- `PERMANENT` — no recovery without manual restore

**ABSOLUTE PROHIBITION:** No LLM is allowed to produce the reversibility classification.
LLM-generated rollback SQL is explicitly prohibited — rollback plans are mechanically derivable
from schema metadata or they do not exist.

---

## 14. Read Impact Gate

**Location in pipeline:** Executes at the very start of RISK_GATE, before the Policy Gate, before any
agent runs.

**Purpose:** Resolve the circular dependency — fast-path routing requires a risk score, but computing a
risk score requires the full pipeline. EXPLAIN provides a preliminary risk signal in under 10 milliseconds
without the full pipeline.

**Procedure:**
1. For every incoming operation (including reads), run `EXPLAIN (FORMAT JSON)` via `postgres-reader`.
2. Extract: estimated row count, estimated cost, scan type (sequential vs. index), partition involvement.
3. Apply routing thresholds:

| Condition | Routing |
|-----------|---------|
| EXPLAIN cost > 50,000 cost units | → Standard review minimum |
| Any table in query tagged PII in policy store | → Standard review minimum |
| Otherwise | → Fast path (auto-execute candidate) |

**CRITICAL:** EXPLAIN runs before routing. It does not run after the full pipeline. Its purpose is to
distinguish trivially safe reads from potentially harmful ones before incurring analysis cost.

---

## 15. Policy Gate

**Location:** Runs inside RISK_GATE after the Read Impact Gate, before any agent executes.

**Purpose:** Evaluate incoming operation against all active policy rules. Any hard rule violation
auto-rejects the operation before the analysis pipeline runs. The auto-rejection is logged in the
audit record with the specific rule cited as the rejection basis.

**Policy enforcement is deterministic. An LLM must never override a hard policy rule.**

### Default Policy Rules (seeded at deployment)

| Rule ID | Condition | Action |
|---------|-----------|--------|
| POLICY_DDL_NO_BACKUP | DROP or TRUNCATE without explicit exception flag AND confirmed backup within 24 hours | AUTO-REJECT |
| POLICY_PII_STANDARD_REVIEW | Any operation on PII-tagged tables | Standard review minimum regardless of row count |
| POLICY_EXTERNAL_INPUT | Operation source_type = external_user_input | Full contract review + prompt injection risk tag |
| POLICY_BULK_DELETE_SENSITIVE | DELETE or UPDATE affecting >10,000 rows on users, orders, or invoices | Full contract review |
| POLICY_PAYMENT_WEBHOOK | Operation fires webhook to external payment processor | Full contract review + mandatory backup confirmation |
| POLICY_AFTER_HOURS | Submission outside 07:00–20:00 local time | Queue until business hours unless on-call approver designated |
| POLICY_AUTO_REJECT_PATTERN | Operation matches previously auto-rejected pattern within 30 days | AUTO-REJECT with historical reason cited |
| POLICY_PII_EXPENSIVE_READ | Read with EXPLAIN cost > 100,000 on PII-tagged tables | Standard review |

---

## 16. Async Approval Protocol

Standard MCP clients cannot hold a synchronous socket open while a human reviews a consequence contract.
ContraGate never blocks the calling agent's connection.

### When Review is Required

The proxy immediately returns to the calling agent:

```json
{
  "status": "PENDING_HUMAN_APPROVAL",
  "approval_id": "cg_7f3a9b12",
  "message": "Action paused for ContraGate consequence review.",
  "poll_url": "/v1/approvals/cg_7f3a9b12/status",
  "sse_url": "/v1/approvals/cg_7f3a9b12/stream",
  "estimated_review_seconds": 300
}
```

### Polling and SSE

The calling agent polls `GET /v1/approvals/{id}/status` or subscribes to
`GET /v1/approvals/{id}/stream` for real-time updates.

- On approval: returns `APPROVED` with the execution result attached.
- On rejection: returns `REJECTED` with the stated reason.

### Original Manifest Binding

The proxy stores the original tool-call manifest keyed by `approval_id`. On approval, the LangGraph
EXECUTION state retrieves the manifest, sends it to the real MCP target server, and posts the result
to the workflow store.

### Non-Negotiable Invariants

| Invariant | Description |
|-----------|-------------|
| No duplicate approval | An approval_id can be approved exactly once |
| No approval replay | A used approval token cannot be reused |
| No duplicate execution | EXECUTION state is idempotent — checks execution_completed flag before proceeding |
| No stale approval | Execution is refused if the contract was modified after approval |
| No mismatched identity | The approved tool-call identity must remain bound to execution — any modification voids the approval |

---

## 17. Human Approval Rules

The React UI has three views:

1. **Approval Queue** — landing page, all pending requests sorted by time remaining before timeout.
   Each card shows: operation intent summary, risk tier badge, primary table, estimated rows, time remaining.

2. **Contract View** — renders the full consequence contract in the four-section format below.

3. **Audit Log** — chronological list of all completed operations with outcomes, stated reasons, and
   blast radius accuracy deltas.

### Four Contract Sections (what the human sees)

**Section 1: What will happen?**
- Operation intent (plain language)
- Primary impact: table, row count (estimated | actual from staging), source condition
- Cascade impact: dependent tables with row counts and cascade type
- Total rows affected across all tables
- Estimated duration
- External actions that will fire and cannot be simulated

**Section 2: What cannot be undone?**
- Reversibility classification (PERMANENT | PARTIAL | REVERSIBLE)
- Database changes explanation
- External effects explanation
- For PERMANENT: `[ I HAVE READ AND UNDERSTOOD THAT THIS ACTION CANNOT BE AUTOMATICALLY REVERSED ]`
  checkbox — **must be checked before Approve button activates**

**Section 3: Has this happened before?**
- Historical precedents (ranked by outcome severity)
- Shows top 3 from three-stage retrieval
- For each: outcome badge, operation ID, date, intent, tables, stated reason

**Section 4: What does the system flag?**
- Policy violations
- Historical rejection warnings
- Simulation warnings (if external trigger cannot be simulated)
- Blast radius confidence with reason

### Decision Options

| Option | Behavior |
|--------|----------|
| **APPROVE** | Reason required (minimum 10 characters). Disabled until reason entered. For PERMANENT ops: disabled until acknowledgement checkbox checked. |
| **REJECT** | Reason required (minimum 10 characters). Logged to audit and memory. |
| **MODIFY WITH CONSTRAINTS** | Opens text input for constraint specification. Triggers selective re-analysis (only stale states re-run). |
| **REQUEST PREREQUISITE** | Opens form to specify required action before re-submission. |

---

## 18. Simulation Architecture

The transaction sandbox executes proposed SQL against a writable staging instance inside an explicit
transaction that is always rolled back.

### Sequence

```
1. begin_sandbox_transaction        → Opens explicit transaction
2. set_sandbox_mode                 → SET LOCAL app.sandbox_mode = 'true'
                                      SET LOCAL statement_timeout = '5000ms'
3. Capture pre-execution row counts → SELECT COUNT(*) on each affected table
4. execute_in_transaction           → Runs proposed SQL
5. Capture post-execution row counts → SELECT COUNT(*) on each affected table
6. read_trigger_log                 → Read sandbox_trigger_log for would-have-fired calls
7. rollback_transaction             → ROLLBACK — production data never changes
```

### Sandbox Mode Flag

Application-level triggers read `current_setting('app.sandbox_mode', true)`. In sandbox mode, instead
of executing the external API call, they log it to `sandbox_trigger_log`. This produces honest simulation:
database-level behavior is captured with real data; external side effects are "would have fired" entries.

### What Simulation Captures

- Actual row count delta for each affected table
- Row count delta for each cascade table
- External calls that would have fired (from sandbox_trigger_log)
- Sequence increment gaps from inserts (noted as low-severity informational items)

### What Simulation Cannot Capture

- Actual execution of non-transactional extensions (these are logged, not executed)
- pg_net / dblink calls are detected and flagged but not simulated

### Staging Instance Requirements

- Must be writable (cannot be a physical read replica)
- Must have the same schema as production
- Must have sandbox-aware triggers installed
- Seed data must produce realistic cascade row counts

---

## 19. Memory Architecture

### Three-Stage Retrieval Implementation

**Stage 1 — Semantic Search**
- Embed the current operation's intent summary using Claude Sonnet via Anthropic Embeddings API
- Embeddings are cached by intent text hash to avoid redundant API calls for identical descriptions
- pgvector cosine similarity search returns top 20 candidates from the memory store
- Tenant-scoped: all queries filter by tenant_id

**Stage 2 — Structural Filter**
- For each of the 20 semantic candidates, compute Jaccard similarity between the current operation's
  affected table set and the candidate's affected table set
- Retain candidates with Jaccard similarity ≥ 0.3 (configurable in policy store)

**Stage 3 — Outcome-Aware Reranking**

| Outcome | Reranking score |
|---------|----------------|
| REJECTED | 1.0 |
| ROLLED_BACK | 0.9 |
| Blast radius accuracy delta > 20% | 0.7 |
| MODIFIED (human changed plan) | 0.5 |
| APPROVED and successful | 0.1 × semantic_similarity_score |

- Return top 3 by reranking score
- These three appear in the contract's historical precedents section
- Top result displayed most prominently

### Reranking Weight Adjustment

After each human decision, a post-decision job checks if the decision reason explicitly references a
surfaced historical operation (by operation ID or semantic similarity to operation description).
- If human's reason confirms relevance: weights for that match type increase by configurable delta
- If human's reason indicates irrelevance: weights decrease by configurable delta
- Weight adjustments run asynchronously and do not block the approval workflow

### Memory Write-Back

After every human decision (including auto-reject), the current operation is written to the memory
store with its outcome. This is how ContraGate learns from every operation.

---

## 20. Audit Architecture

### Audit Record Contents

Every completed operation's audit record contains:
- Full HandoffContract at time of completion
- Original tool-call manifest
- Human decision with reason and timestamp
- Execution result (or rejection reason)
- Post-execution actual row counts
- Blast radius accuracy delta
- Agent workflow provenance trail

### Tamper-Evidence

The `audit-logger` MCP server enforces:
- Append-only writes (no UPDATE, no DELETE from normal code paths)
- Row-level SHA-256 checksums on write
- Administrative deletion requires separate authorization path with audit trail

### Audit Log UI

The AuditLog page shows:
- Chronological list of all completed operations
- Outcome badges (AUTO, APPROVED, REJECTED, ROLLED_BACK, MODIFIED)
- Stated reasons
- Blast radius accuracy deltas — this is the visible feedback loop in the demo

---

## 21. Post-Execution Feedback Loop

After every EXECUTION state completes, ContraGate runs a post-execution verification job.

### Procedure

1. Query actual affected row count from production audit log (or from a post-execution SELECT COUNT on
   affected tables)
2. Compute accuracy delta: `(actual - estimated) / estimated`
3. If `|delta| > 10%`: log the discrepancy to the memory store against the specific table and operation type

### Confidence Score Adjustment

| Direction | Adjustment |
|-----------|-----------|
| Underestimate (actual > estimated, delta > 10%) | Lower confidence by **0.05** |
| Overestimate (actual < estimated, delta > 10%) | Lower confidence by **0.02** |

**Rationale:** Underestimates are treated as more severe because they systematically mislead approvers
toward thinking operations are smaller than they are.

### Effect on Future Operations

A lower confidence score on a table:
- Raises the risk tier threshold for that table's operations
- Adds a "confidence reduced by prior inaccuracy" flag in the system flags section of the contract

### Initial Confidence Score

- `0.8` — for tables with statistics updated within the last 24 hours
- Lower — for tables with stale statistics (graduated by staleness)

---

## 22. Security Invariants

These are NON-NEGOTIABLE. Any implementation that violates these invariants must be rejected.

| # | Invariant |
|---|-----------|
| 1 | No unapproved production execution — EXECUTION state checks approval before any write |
| 2 | No LLM-controlled authoritative risk classification — all risk tiers are deterministic code |
| 3 | No LLM-generated rollback SQL — rollback is mechanically derived or does not exist |
| 4 | No production writes from simulation — sandbox always uses staging database |
| 5 | No sandbox external network egress — blocked at infrastructure level |
| 6 | No cross-tenant memory retrieval — every memory query filters by tenant_id |
| 7 | No mutable audit history — audit-logger enforces append-only with checksums |
| 8 | No execution of stale contracts — approval voids if contract was modified after approval |
| 9 | No approval replay — approval tokens are single-use |
| 10 | No duplicate execution — EXECUTION state is idempotent with execution_completed flag |
| 11 | No trust of raw SQL as LLM instruction — raw SQL is treated as data, wrapped in untrusted_input tags |
| 12 | All external/user/agent input is untrusted — all source_type=external_user_input gets prompt injection risk tag |
| 13 | Prompt injection must never override deterministic controls — LLM output is consumed as structured JSON, not instructions |
| 14 | Every important action must have provenance — workflow_provenance is required on every agent write |
| 15 | The original approved tool-call identity must remain bound to execution — manifest hash is verified before execution |

---

## 23. Testing Strategy

### Unit Tests (sql_analysis_lib)

All must pass before Phase 2 begins. These are pure function tests with no external dependencies.

| Test file | Coverage |
|-----------|---------|
| `test_cascade.py` | FK cascade graph construction, multi-level cascades, depth limit |
| `test_estimator.py` | Row count estimation, confidence scoring, stale statistics handling |
| `test_triggers.py` | Trigger detection, non-transactional extension classification |
| `test_reversibility.py` | All four classifications, priority order enforcement, edge cases |
| `test_explain_parser.py` | EXPLAIN JSON parsing, cost extraction, scan type detection |

All 30 fixture scenarios must produce correct expected outputs.

### Integration Tests

| Area | Tests |
|------|-------|
| PostgreSQL + pgvector | Memory store operations, embedding storage and retrieval |
| MCP servers | Each server's tool schema validation and permission enforcement |
| Staging sandbox | BEGIN/execute/capture/ROLLBACK cycle with real SQL |
| Policy store | Rule loading, rule evaluation, auto-reject behavior |
| Audit logger | Append-only enforcement, checksum generation and verification |

### Workflow Tests

Every state transition must have a test:
- Normal flow: INTAKE → RISK_GATE → ANALYSIS → CONTEXT_AND_SIM → CONTRACT → HUMAN_REVIEW → EXECUTION → AUDIT
- Auto-execute path: INTAKE → RISK_GATE → AUTO_EXECUTE → AUDIT
- Policy auto-reject: INTAKE → RISK_GATE → AUDIT (with rejection reason)
- Human reject: HUMAN_REVIEW → REJECT → AUDIT
- Human timeout: HUMAN_REVIEW (30 min) → AUTO-REJECT → AUDIT
- Modify path: HUMAN_REVIEW → MODIFY → ANALYSIS (partial) → CONTEXT_AND_SIM (partial) → CONTRACT → HUMAN_REVIEW
- Sandbox timeout retry: CONTEXT_AND_SIM (timeout × 2) → CONTRACT (simulation unavailable)
- Memory unavailable: CONTEXT_AND_SIM (memory down) → CONTRACT (retrieval unavailable)

### End-to-End Tests (Four Demo Scenarios)

| Scenario | Description |
|----------|-------------|
| **E2E-1** | Fast-path SELECT — EXPLAIN cost 1,840, no PII, auto-executes in < 3 seconds |
| **E2E-2** | Standard UPDATE + approval — UPDATE users, 3,847 rows, REVERSIBLE, STANDARD tier, human approves |
| **E2E-3** | Dangerous DELETE + historical rejection — DELETE users, PERMANENT, cg_1847 surfaces as rejection, human rejects |
| **E2E-4** | MODIFY WITH CONSTRAINTS + selective re-analysis — DELETE orders, FULL CONTRACT, human modifies, tier drops to STANDARD |

Tests must verify actual behavior — not mocked happy paths. Scenario 3 must verify that the exact
seeded operation `cg_1847` surfaces as the top retrieval result.

---

## 24. Local Development Architecture

Seven Docker containers on a single developer machine.

| Container | Purpose | Port | Health Check |
|-----------|---------|------|-------------|
| `contragate-db` | PostgreSQL 16 with pgvector. Hosts application DB (memory store, audit log, policy store) and staging DB (separate schema). | 5432 | `pg_isready` |
| `contragate-proxy` | MCP proxy entry point. Tool call interception, async approval protocol, polling endpoints, SSE streams. | 8000 | GET /health |
| `contragate-orchestrator` | LangGraph state machine. Receives workflow requests from proxy, manages agent invocations. | 8001 | GET /health |
| `contragate-agents` | Three agents. Stateless per invocation — all state flows through the typed handoff contract. | 8002 | GET /health |
| `contragate-mcp` | All six MCP servers as a single multi-server container for local dev. (In production, each runs independently.) | 8010–8015 | Per-server |
| `contragate-ui` | React app. Serves on port 3000, proxying API calls to contragate-proxy. | 3000 | HTTP 200 |
| `contragate-notifier` | Local mock notifier. Prints approval requests to terminal. Accepts decisions at POST /dev/approve and POST /dev/reject. | 8020 | GET /health |

### Startup Order

```
contragate-db (must be healthy)
  → contragate-mcp (depends on db)
  → contragate-orchestrator (depends on mcp)
  → contragate-agents (depends on mcp, orchestrator)
  → contragate-proxy (depends on orchestrator)
  → contragate-notifier (depends on proxy)
  → contragate-ui (depends on proxy)
```

### Database Separation

Application database and staging database are separate PostgreSQL schemas within the same instance for
local development. In production, they are separate Railway PostgreSQL instances.

---

## 25. Environment Variables

All required in `.env` for local development:

```bash
# Required for all environments
ANTHROPIC_API_KEY=           # Claude Sonnet API key — for LLM calls and embeddings
DATABASE_URL=                # Application database connection string (app schema)
STAGING_DATABASE_URL=        # Staging database connection string (staging schema)
CONTRAGATE_TENANT_ID=demo_tenant  # Tenant identifier (default: demo_tenant)

# Required for production (Railway) only
SLACK_BOT_TOKEN=             # Required for online deployment Slack notifier
SLACK_APPROVAL_CHANNEL=      # Slack channel for approval notifications

# Optional / overrides
CONTRAGATE_TIMEOUT_SECONDS=1800    # Human review timeout (default: 1800 = 30 min)
CONTRAGATE_EXPLAIN_THRESHOLD=50000 # Read Impact Gate cost threshold (default: 50000)
SANDBOX_STATEMENT_TIMEOUT=5000     # Sandbox statement timeout in ms (default: 5000)
JACCARD_SIMILARITY_THRESHOLD=0.3   # Memory retrieval structural filter threshold
```

---

## 26. Deployment Architecture (Railway)

Two PostgreSQL instances:
- Application database (memory store, audit log, policy store)
- Staging database (simulation sandbox)

Four application services:
- `contragate-proxy-orchestrator` — combined proxy and orchestrator. Exposes the public URL that
  external agents point their MCP client at.
- `contragate-agents` — agent service. Scaled to two instances for availability.
- `contragate-mcp` — all six MCP servers. In production, each can be scaled independently.
- `contragate-ui` — React app served from Railway's static hosting.

Seed scripts run against Railway databases after deployment:
```bash
railway run psql $DATABASE_URL -f seed/demo_schema.sql
railway run psql $DATABASE_URL -f seed/historical_operations.sql
railway run psql $DATABASE_URL -f seed/default_policies.sql
railway run psql $STAGING_DATABASE_URL -f seed/staging_schema.sql
railway run psql $STAGING_DATABASE_URL -f seed/sandbox_trigger.sql
```

---

## 27. Coding Conventions

### Language and Runtime

- **Backend:** Python 3.12+ (proxy, orchestrator, agents, MCP servers, sql_analysis_lib)
- **Frontend:** React 18 with Vite (ui/)
- **Schema validation:** Pydantic v2 for all Python data structures
- **Orchestration:** LangGraph (latest stable)
- **LLM:** Anthropic Python SDK (claude-sonnet-* — check `docs/IMPLEMENTATION_PLAN.md` for current model ID)
- **Database:** asyncpg for async PostgreSQL access; psycopg2 for synchronous sql_analysis_lib

### Python Style

- Type annotations on all function signatures
- Pydantic models for all structured data (no raw dicts passed between agents)
- `async`/`await` throughout (except sql_analysis_lib, which is synchronous for unit testability)
- No bare `except:` — always catch specific exception types
- No `print()` in production code — use Python `logging` module with structured output

### Error Handling Rules

- At system boundaries (agent inputs, MCP tool outputs, external API responses): validate and raise
  typed exceptions
- Do not catch exceptions just to suppress them — let failures propagate to the orchestrator's
  retry/fallback logic
- Simulation timeout must be caught and handled with retry — never propagated as an unhandled crash
- Memory store unavailability must be caught and handled with graceful degradation — never crashes the pipeline

### Observability

- Every state transition in LangGraph emits a structured log event with: operation_id, state, timestamp, duration
- Every MCP tool call is logged (tool name, server, duration, success/failure) — this is automatic via the MCP protocol
- The HandoffContract `workflow_provenance` field is the primary observability artifact — it contains
  the full lineage of every field

---

## 28. Seeded Historical Operations

The `seed/historical_operations.sql` file contains exactly 20 seeded operations:

### 5 REJECTED
| ID | SQL | Tables | Reason |
|----|-----|--------|--------|
| cg_1847 | DELETE FROM users WHERE last_active older than 2 years | users, orders, invoices | "Cascade into invoices caused loss of 8,200 billing records. Archival step required before any delete." |
| cg_2203 | DELETE FROM users WHERE subscription_status = 'cancelled' | users, notifications | "SendGrid webhook fired 11,000 calls before operator noticed. 3 hours to suppress campaign." (stored as ROLLED_BACK with rollback flag) |
| cg_3891 | TRUNCATE orders | orders | "No backup confirmed. Policy violation — auto-rejected by ContraGate before human review." |
| cg_4102 | UPDATE users SET email WHERE source = external_input | users | "Prompt injection risk tag present. External input source — rejected pending security review." |
| cg_5517 | DELETE FROM orders WHERE status = 'cancelled' AND created_at > 3 years | orders, invoices | "Cascade into invoices not acceptable. Need archival to cold storage first." |

### 5 ROLLED_BACK
Five operations with blast radius accuracy deltas above 30% — actual rows significantly exceeded
estimated rows. These produce low confidence scores for the affected tables.

### 5 APPROVED and Successful
Five operations on smaller row counts with no cascades or external triggers.

### 5 MODIFIED
Five operations where the human chose Modify With Constraints and the system ran selective re-analysis.

---

## 29. Git and Commit Expectations

- Branch from `main` for each phase
- Commit message format: `feat(phase-N): <description>` or `fix(component): <description>`
- No secrets in commits — `.env` is gitignored, `.env.example` contains only key names
- No generated files committed (no `__pycache__`, no `node_modules`, no `.pyc`)
- Each phase ends with a commit that makes the test suite pass for that phase's deliverables

---

## 30. Phase-by-Phase Implementation Order

| Phase | Deliverable | Entry Criterion | Exit Criterion |
|-------|------------|----------------|----------------|
| **Phase 0** | Architecture + repository foundation (this file + ADRs + structure) | Empty repo | CLAUDE.md exists, all stubs created, no secrets, .gitignore correct |
| **Phase 1** | `sql_analysis_lib` — all 5 modules, all 30 fixtures, all tests passing | Phase 0 complete | 100% of fixture tests pass against a real PostgreSQL instance |
| **Phase 2** | Docker Compose + MCP servers + LangGraph skeleton + async protocol | Phase 1 complete | All 7 containers run, health checks pass, smoke test: hardcoded tool call flows end-to-end with stubs |
| **Phase 3** | Analyzer Agent — all tools as thin wrappers over sql_analysis_lib, prompt injection boundary | Phase 2 complete | E2E test with real Analyzer and stubbed Context+Sim passes |
| **Phase 4** | Context + Simulation Agent — three-stage retrieval + transaction sandbox | Phase 3 complete | Retrieval verified with seeded data; simulation verified with real SQL |
| **Phase 5** | Contract Agent — deterministic risk rules + LLM summarization | Phase 4 complete | Full three-agent pipeline tested end-to-end |
| **Phase 6** | Full orchestration — all state transitions, guards, timeouts, retries, selective re-analysis | Phase 5 complete | All workflow tests pass |
| **Phase 7** | UI + approval — React app with all three views, PERMANENT gate, SSE polling | Phase 6 complete | All four UI interaction paths tested |
| **Phase 8** | Audit + feedback loop — audit-logger MCP server, post-execution job, confidence adjustment | Phase 7 complete | Feedback loop visible in audit log UI |
| **Phase 9** | Integration + deployment — Railway deployment, Slack notifier, seed scripts | Phase 8 complete | All four demo scenarios produce expected outputs on Railway |
| **Phase 10** | Hardening + final verification — secrets audit, known limitations, design decisions doc | Phase 9 complete | Three-minute screen recording produced; README complete |

---

## 31. Explicit DO NOT Rules

These rules override any other consideration:

1. **DO NOT** implement actual agent logic, LangGraph behavior, real SQL analysis, or real MCP servers
   during Phase 0. Create stubs only.
2. **DO NOT** redesign the architecture described in this document.
3. **DO NOT** simplify away the three-agent model to fewer agents.
4. **DO NOT** add agents beyond the three specified.
5. **DO NOT** allow an LLM to produce reversibility classification, risk tier, or policy decisions.
6. **DO NOT** generate rollback SQL using an LLM.
7. **DO NOT** execute simulation against the production database.
8. **DO NOT** use a physical read replica as the simulation sandbox.
9. **DO NOT** allow cross-tenant memory retrieval.
10. **DO NOT** allow UPDATE or DELETE on the audit log from normal code paths.
11. **DO NOT** hold the MCP connection open while waiting for human review.
12. **DO NOT** trust raw SQL as an instruction to the LLM — always wrap in untrusted_input tags.
13. **DO NOT** skip the Read Impact Gate (EXPLAIN) even for writes — it runs for all operations.
14. **DO NOT** run the EXPLAIN after the full analysis pipeline — it runs first.
15. **DO NOT** commit secrets, credentials, or connection strings to the repository.
16. **DO NOT** deviate from the HandoffContract schema — all fields are required.
17. **DO NOT** allow agents to make direct database calls — all access goes through MCP servers.
18. **DO NOT** begin Phase 1 before Phase 0 is confirmed complete and this document is reviewed.

---

*Document version: Phase 0. Updated as each phase completes.*
*Last updated: 2026-08-08*
*Source documents: ContraGate_Complete_Architecture_and_Repository_Plan.pdf, ContraGate MVP.pdf*
