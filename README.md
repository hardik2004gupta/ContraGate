<div align="center">

<img src="docs/brand/02-contragate-primary-corporate-logo.png" alt="ContraGate" width="280" />

# ContraGate

**Know the consequences before the action executes.**

ContraGate is a consequence-aware execution gate for AI agents. It intercepts MCP tool calls, computes blast radius, retrieves historical precedent, simulates mutations in a transaction sandbox, and presents humans with a risk-tiered consequence contract — before any action reaches production.

<br />

[![Python](https://img.shields.io/badge/Python-3.12+-3776AB?style=flat-square&logo=python&logoColor=white)](https://python.org)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![LangGraph](https://img.shields.io/badge/LangGraph-0.2+-7C3AED?style=flat-square)](https://github.com/langchain-ai/langgraph)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-16+-4169E1?style=flat-square&logo=postgresql&logoColor=white)](https://postgresql.org)
[![pgvector](https://img.shields.io/badge/pgvector-0.3+-0EA5E9?style=flat-square)](https://github.com/pgvector/pgvector)
[![React](https://img.shields.io/badge/React-18-61DAFB?style=flat-square&logo=react&logoColor=black)](https://react.dev)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED?style=flat-square&logo=docker&logoColor=white)](https://docs.docker.com/compose/)
[![Tests](https://img.shields.io/badge/tests-643%20passed-22C55E?style=flat-square)](tests/)

<br />

[Overview](#overview) · [Architecture](#architecture) · [Quick Start](#quick-start) · [Demo](#product-tour) · [Testing](#testing) · [Security](#security-model) · [Limitations](#known-limitations)

</div>

---

<p align="center">
  <img
    src="docs/readme/05-contragate-enterprise-ai-infrastructure-hero.png"
    alt="ContraGate — consequence-aware execution gate for AI agents"
    width="900"
  />
</p>

---

## Overview

Every major agent framework includes a human approval step. Without exception, they show the human what the agent *wants* to do — not what will actually happen.

ContraGate builds the missing layer: the consequence-aware gate between agent intent and production execution.

| Without ContraGate | With ContraGate |
|---|---|
| "Approve: DELETE FROM users WHERE last_active < 2 years ago" | Cascade into `orders` and `invoices` — **12,400 rows** across 3 tables. 11,000 webhook calls would fire. Operation is **PERMANENT**. Prior rejection for this pattern on 2025-11-14. |
| Human approves or rejects blind. | Human sees exactly what happens, what can't be undone, and what happened last time. |

<p align="center">
  <img
    src="docs/readme/07-simple-intent-complex-consequences.png"
    alt="Simple intent, complex consequences — the gap ContraGate fills"
    width="720"
  />
</p>

**Three failure modes ContraGate solves:**

- **Uninformed approval** — humans approve without visibility into row counts, cascades, or external effects.
- **Approval fatigue** — thin context trains humans to approve reflexively rather than deliberately.
- **Institutional amnesia** — every approval is made in isolation; historical outcomes are never surfaced.

---

## Table of Contents

- [Architecture](#architecture)
- [Three-Agent Pipeline](#three-agent-pipeline)
- [Risk & Safety Model](#risk--safety-model)
- [Consequence Contract](#consequence-contract)
- [Historical Memory](#historical-memory)
- [Transaction Sandbox](#transaction-sandbox)
- [Async Approval Protocol](#async-approval-protocol)
- [Product Tour](#product-tour)
- [Demo Scenarios](#demo-scenarios)
- [Quick Start](#quick-start)
- [Environment Variables](#environment-variables)
- [Running the Demo](#running-the-demo)
- [Testing](#testing)
- [Project Structure](#project-structure)
- [Deployment](#deployment)
- [Security Model](#security-model)
- [Design Decisions](#design-decisions)
- [Known Limitations](#known-limitations)
- [Roadmap](#roadmap)
- [Contributing](#contributing)

---

## Architecture

<p align="center">
  <img
    src="docs/readme/06-contragate-software-architecture-diagram.png"
    alt="ContraGate software architecture diagram"
    width="860"
  />
</p>

ContraGate sits transparently between an agent's MCP tool calls and the real execution target. The calling agent never changes — it sends the same JSON-RPC call it always did. ContraGate intercepts at the proxy layer, runs consequence analysis, and either executes immediately (safe reads) or routes to human review.

```
External Agent (MCP Client)
        │  JSON-RPC tool call
        ▼
ContraGate MCP Proxy          ← port 8000; never blocks the caller
        │
        ├─► Risk Gate
        │     ├─ Read Impact Gate   EXPLAIN in <10ms — breaks fast/slow routing
        │     └─ Policy Gate        deterministic rule engine — auto-reject or route
        │
        │  if auto-execute → EXECUTION → AUDIT (< 3 seconds)
        │  if review required ↓
        ▼
LangGraph Orchestrator
        │
        ├─ ANALYSIS          Analyzer Agent
        ├─ CONTEXT_AND_SIM   Context + Simulation Agent  (retrieval ‖ sandbox)
        └─ CONTRACT          Contract Agent
                │
                ▼
        Human Review (React UI · Slack)
                │
        APPROVE / REJECT / MODIFY / REQUEST PREREQUISITE
                │
        EXECUTION  →  AUDIT  →  Memory write-back  →  Feedback loop
```

**Seven local containers, one command:**

| Container | Role | Port |
|---|---|---|
| `contragate-db` | PostgreSQL 16 + pgvector | 15432 (host) |
| `contragate-proxy` | MCP proxy, polling/SSE endpoints | 8000 |
| `contragate-orchestrator` | LangGraph state machine | 8001 |
| `contragate-agents` | Three-agent service | 8002 |
| `contragate-mcp` | Six MCP servers | 8010–8014 |
| `contragate-ui` | React approval UI | 3000 |
| `contragate-notifier` | Local mock notifier (Slack in prod) | 8020 |

---

## Three-Agent Pipeline

Each agent has a bounded responsibility. Mixing them would allow LLM discretion in the wrong places — see [ADR-001](docs/ADR/ADR-001-three-agents.md).

### Analyzer Agent

Converts the intercepted tool call into a structured consequence map.

| Tool | Purpose |
|---|---|
| `parse_sql_intent` | Extract operation type, tables, conditions |
| `validate_schema` | Confirm targets exist in production schema |
| `count_affected_rows` | Estimate rows via `pg_stat_user_tables` |
| `trace_foreign_keys` | Full FK cascade graph from `information_schema` |
| `list_cascade_tables` | Ordered dependent tables with row estimates |
| `estimate_api_fanout` | Volume of external API calls from webhook triggers |
| `classify_operation_type` | DDL / bulk-write / selective-write / read |
| `detect_trigger_side_effects` | Identify triggers invoking non-transactional extensions |

All SQL analysis runs through `sql_analysis_lib` — a standalone, fully unit-tested Python library with 30 fixture scenarios covering every operation type.

### Context & Simulation Agent

Runs **retrieval and simulation in parallel** within a single LangGraph state.

**Retrieval tools** (three-stage — see [Historical Memory](#historical-memory)):

| Tool | Purpose |
|---|---|
| `semantic_search_memory` | Cosine similarity over pgvector embeddings of past intents |
| `filter_by_table_overlap` | Jaccard similarity on affected table sets |
| `rerank_by_outcome_severity` | Float rejected/rolled-back operations to top |

**Simulation tools:**

| Tool | Purpose |
|---|---|
| `begin_sandbox_transaction` | Open explicit transaction on staging instance |
| `set_sandbox_mode` | `SET LOCAL app.sandbox_mode = 'true'` + 5s timeout |
| `execute_in_transaction` | Run proposed SQL inside the open transaction |
| `capture_row_diff` | Before/after row counts for each affected table |
| `read_trigger_log` | External calls that would have fired |
| `rollback_transaction` | Always rolled back — production data is never touched |

### Contract Agent

Assembles all outputs into a consequence contract and applies risk classification.

> **Risk classification is deterministic. The LLM does not decide whether an operation is safe.**

The rule engine is a pure Python function with typed inputs that returns a `RiskTier` enum. An LLM produces only the human-readable prose summary of already-computed structured fields. See [ADR-003](docs/ADR/ADR-003-deterministic-risk.md).

---

## Risk & Safety Model

### Routing Tiers

| Tier | Condition | Human Review |
|---|---|---|
| **AUTO\_EXECUTE** | Read op, EXPLAIN cost < 50,000, no PII table | No — executes in < 3s |
| **STANDARD\_REVIEW** | Reversible write, risk score 0.3–0.7, no historical rejection | Yes |
| **FULL\_CONTRACT** | PERMANENT reversibility, cascade > threshold, external triggers, historical rejection, injection risk | Yes — full contract required |

### Reversibility Classifications

Applied in strict priority order — no LLM discretion:

```
1. DDL (DROP, ALTER, TRUNCATE)                              → PERMANENT
2. Non-transactional external effects (pg_net, dblink)      → PERMANENT for those effects
3. DELETE/UPDATE without soft-delete column + no PITR       → PERMANENT
4. Snapshot-feasible operations                             → REVERSIBLE_AUTOMATED
5. PITR-recoverable operations                              → REVERSIBLE_PITR
6. Mixed recoverable/permanent components                   → PARTIAL
```

**LLM-generated rollback SQL is explicitly prohibited.** Rollback plans are mechanically derivable from schema metadata or they do not exist. See [ADR-008](docs/ADR/ADR-008-no-llm-rollback.md).

### Gates Before the Pipeline

**Read Impact Gate** — `EXPLAIN (FORMAT JSON)` runs in under 10ms as the first step of RISK\_GATE, before any agent invocation. This breaks the routing circular dependency: fast-path routing requires a risk signal, but computing a risk signal requires the full pipeline. EXPLAIN provides the signal cheaply. See [ADR-005](docs/ADR/ADR-005-explain-gate.md).

**Policy Gate** — eight seeded deterministic policy rules enforce auto-rejection and minimum review tiers before agents run. Policy violations are always cited by rule ID in the audit record.

**Prompt Injection Boundary** — raw SQL is never passed to an LLM as an instruction. All agent LLM calls wrap SQL content in `<untrusted_input>` boundary tags. Structured output is consumed as typed JSON, never as text instructions.

---

## Consequence Contract

The human does not see a raw SQL statement. They see a structured four-section contract:

```
┌─────────────────────────────────────────────────────────────────┐
│  ● FULL CONTRACT   ⚠ PERMANENT   cg_7f3a9b12                   │
│  Delete inactive users — last_active older than 2 years        │
└─────────────────────────────────────────────────────────────────┘

§1  WHAT WILL HAPPEN?
    Operation:   DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'
    Primary:     users — ~8,400 rows (estimated) / 8,212 rows (staged)
    Cascade:     orders → 3,100 rows  ·  invoices → 1,088 rows
    Total:       12,400 rows across 3 tables
    External:    SendGrid webhook — ~8,400 calls would fire

§2  WHAT CANNOT BE UNDONE?
    ⛔ PERMANENT — no automated recovery path
    Reason:  users table has no soft-delete column (deleted_at)
             Non-transactional SendGrid trigger detected

    [ I HAVE READ AND UNDERSTOOD THAT THIS ACTION CANNOT
      BE AUTOMATICALLY REVERSED ]  ☐                            ← must check to unlock Approve

§3  HAS THIS HAPPENED BEFORE?
    ● REJECTED  cg_1847  2025-11-14
      "Cascade into invoices caused loss of 8,200 billing records.
       Archival step required before any delete."

§4  WHAT DOES THE SYSTEM FLAG?
    POLICY_BULK_DELETE_SENSITIVE triggered (>10,000 rows on users)
    Historical rejection for analogous operation surfaced
    Blast radius confidence: 0.74 (pg_stat updated 18h ago)
```

Each section maps to a collapsible UI component. Section 4 auto-expands when policy violations or injection risk is present.

---

## Historical Memory

Three-stage retrieval ensures relevant precedent surfaces — and that what went wrong before surfaces first.

```
Intent summary
      │
      ▼
Stage 1 — Semantic Search
      pgvector cosine similarity → top 20 candidates
      (embeddings cached by intent hash — no redundant API calls)
      │
      ▼
Stage 2 — Structural Filter
      Jaccard similarity on affected table sets ≥ 0.3 → retain
      │
      ▼
Stage 3 — Outcome Reranking
      REJECTED        → 1.0
      ROLLED_BACK     → 0.9
      Delta > 20%     → 0.7
      MODIFIED        → 0.5
      APPROVED (ok)   → 0.1 × semantic_score
      │
      ▼
Top 3 surface in contract §3
```

> ContraGate does not merely retrieve similar operations. It prioritizes what went wrong before.

After each human decision, weights adjust based on whether the human's stated reason referenced the surfaced precedent — building a feedback loop over time.

---

## Transaction Sandbox

The simulation executes against real staging data, not synthetic fixtures.

```
1. BEGIN                          opens explicit transaction
2. SET LOCAL app.sandbox_mode = 'true'
   SET LOCAL statement_timeout = '5000ms'
3. Capture pre-execution row counts (per affected table)
4. Execute proposed SQL
5. Capture post-execution row counts
6. Read sandbox_trigger_log       external calls that would have fired
7. ROLLBACK                       production is never touched
```

Application-level triggers read `current_setting('app.sandbox_mode', true)`. In sandbox mode, instead of executing external API calls, they log to `sandbox_trigger_log`. This gives honest simulation: database behavior is captured with real data; external side effects are "would have fired" entries.

**What simulation captures:** actual row delta per table, cascade row counts, external call parameters.

**What simulation cannot capture:** actual execution of non-transactional extensions. `pg_net`/`dblink` calls are detected and flagged, not executed — the sandbox has no network egress by design.

The sandbox requires a **writable staging instance**, never a physical read replica. PostgreSQL read replicas reject writes even inside a `ROLLBACK`-planned transaction. See [ADR-002](docs/ADR/ADR-002-transaction-sandbox.md).

---

## Async Approval Protocol

Standard MCP clients cannot hold a connection open while a human reviews a consequence contract. ContraGate never blocks the caller.

**Immediate response on review required:**

```json
{
  "status": "PENDING_HUMAN_APPROVAL",
  "approval_id": "cg_7f3a9b12",
  "message": "Action paused for ContraGate consequence review.",
  "poll_url": "/v1/approvals/cg_7f3a9b12/status",
  "sse_url":  "/v1/approvals/cg_7f3a9b12/stream",
  "estimated_review_seconds": 300
}
```

The caller polls `GET /v1/approvals/{id}/status` or subscribes to `GET /v1/approvals/{id}/stream` for real-time SSE updates. On approval, the EXECUTION state retrieves the **original tool-call manifest** by `approval_id` — execution never trusts data from the approval payload itself.

**Security invariants on the approval token:**

- An `approval_id` can be decided exactly once (no replay)
- A used approval token cannot be reused
- Execution is idempotent — the `execution_completed` flag is checked before any write
- Execution is refused if the contract was modified after approval (manifest hash check)

No human decision within 30 minutes → auto-reject with reason logged to audit. See [ADR-004](docs/ADR/ADR-004-async-approval.md).

---

## Product Tour

### Approval Workflow

The approver sees the consequence contract, reviews all four sections, and makes a decision with a required reason. For PERMANENT operations, an acknowledgement checkbox gates the Approve button.

<p align="center">
  <img
    src="docs/readme/10-contragate-approvals-review-demo.gif"
    alt="ContraGate consequence contract approval workflow"
    width="860"
  />
</p>

### Audit Feedback Loop

Every completed operation — approved, rejected, or auto-executed — lands in the audit log with its outcome, stated reason, and blast radius accuracy delta. This delta feeds back into future confidence scores: underestimates lower confidence more than overestimates, because they mislead approvers toward thinking operations are smaller than they are.

<p align="center">
  <img
    src="docs/readme/09-contragate-audit-log-demo.gif"
    alt="ContraGate audit log with blast radius accuracy deltas"
    width="860"
  />
</p>

Rejected operations write back to the memory store. The next time a similar operation is submitted, that rejection surfaces as a historical precedent in §3 of the new contract.

---

## Demo Scenarios

Four scenarios cover the full routing surface:

| # | Operation | Route | Demonstrates |
|---|---|---|---|
| **E2E-1** | `SELECT * FROM orders WHERE id = 123` | AUTO\_EXECUTE | Fast path — EXPLAIN cost 1,840, no PII, executes in < 3 seconds |
| **E2E-2** | `UPDATE users SET last_login = NOW() WHERE id = 42` | STANDARD\_REVIEW | Human approval of a reversible, bounded write |
| **E2E-3** | `DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'` | FULL\_CONTRACT | PERMANENT operation with historical rejection `cg_1847` surfacing |
| **E2E-4** | `DELETE FROM orders WHERE status = 'cancelled' AND created_at < NOW() - INTERVAL '3 years'` | FULL → STANDARD | Modify With Constraints triggers selective re-analysis; only stale states re-run |

E2E-3 verifies that the seeded rejection `cg_1847` surfaces as the top retrieval result. This requires the Anthropic Embeddings API (`ANTHROPIC_API_KEY` set).

---

## Quick Start

**Requirements:** Docker Desktop, Python 3.12+, `ANTHROPIC_API_KEY`

```bash
git clone https://github.com/hardik2004gupta/ContraGate.git
cd ContraGate
```

```bash
cp .env.example .env
# Edit .env — add your ANTHROPIC_API_KEY
```

```bash
make up
# Builds images and starts all 7 containers.
# Allow ~45 seconds for health checks to pass.
```

The seed data (demo schema, 20 historical operations, 8 policy rules) is applied automatically when the database container initializes for the first time.

```bash
open http://localhost:3000   # macOS
# or: xdg-open http://localhost:3000   (Linux)
# or: start http://localhost:3000      (Windows)
```

**Verify health:**

```bash
curl http://localhost:8000/health
curl http://localhost:8001/health
curl http://localhost:8002/health
```

**Re-apply seeds** (if needed after `make db-reset`):

```bash
make seed
```

---

## Environment Variables

Variables present in `.env.example`:

| Variable | Required | Default | Purpose |
|---|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | — | LLM calls and pgvector embeddings |
| `DATABASE_URL` | Yes | — | Application DB (memory store, audit log, policy store) |
| `STAGING_DATABASE_URL` | Yes | — | Sandbox DB — **must not equal** `DATABASE_URL` in production |
| `CONTRAGATE_TENANT_ID` | No | `demo_tenant` | Tenant identifier |
| `DEV_MODE` | No | `true` | `true` = in-memory workflow store fallback; `false` = DB required |
| `CONTRAGATE_TIMEOUT_SECONDS` | No | `1800` | Human review timeout (30 min) |
| `CONTRAGATE_EXPLAIN_THRESHOLD` | No | `50000` | EXPLAIN cost unit threshold for fast-path routing |
| `SANDBOX_STATEMENT_TIMEOUT` | No | `5000` | Sandbox statement timeout (ms) |
| `JACCARD_SIMILARITY_THRESHOLD` | No | `0.3` | Stage 2 structural filter threshold |
| `SLACK_BOT_TOKEN` | Prod only | — | Slack notifier |
| `SLACK_APPROVAL_CHANNEL` | Prod only | — | Channel for approval notifications |
| `SLACK_SIGNING_SECRET` | Prod only | — | Validates incoming Slack webhook signatures |
| `USE_MOCK_AUTH` | No | `true` | `true` bypasses API key auth in local dev |
| `USE_MOCK_NOTIFIER` | No | `true` | `true` uses terminal mock instead of Slack |

When running tests **from the host** (not from inside Docker), the PostgreSQL container is reachable at `localhost:15432`:

```bash
DATABASE_URL=postgresql://contragate:contragate_local@localhost:15432/contragate
```

---

## Running the Demo

**1. Start the stack and open the UI:**

```bash
make up
open http://localhost:3000
```

**2. Submit E2E-1 (fast path):**

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"execute_query","arguments":{"sql":"SELECT id, email FROM users LIMIT 5"}}}'
# Returns: {"result": {...}, "status": "AUTO_EXECUTED"}
```

**3. Submit E2E-2 (approval):**

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"execute_query","arguments":{"sql":"UPDATE users SET last_login = NOW() WHERE id = 42"}}}'
# Returns: {"status": "PENDING_HUMAN_APPROVAL", "approval_id": "cg_...", ...}
```

Go to the Approval Queue in the UI. Review the contract. Approve with a reason.

**4. Submit E2E-3 (dangerous delete with rejection):**

```bash
curl -X POST http://localhost:8000/mcp \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"execute_query","arguments":{"sql":"DELETE FROM users WHERE last_active < NOW() - INTERVAL '\''2 years'\''"}}}'
```

In the contract view: observe `cg_1847` in §3 (Has this happened before?). Reject with reason. Verify the rejection appears in the Audit Log.

**5. Inspect the audit log:**

Open `http://localhost:3000/audit` to see all completed operations with blast radius accuracy deltas.

---

## Testing

```bash
# Unit + integration — no Docker required
make test

# Full test suite — requires Docker DB on localhost:15432
make test-full

# E2E scenarios — requires full Docker stack
make test-e2e

# All tests
make test-all
```

**Baseline (v1.0.0-rc.1):**

| Suite | Passed | Skipped | Notes |
|---|---|---|---|
| Unit | 343 | 0 | No external deps |
| Integration | 187 | 11 | Skipped without live PostgreSQL |
| SQL Analysis Lib | 100 | 42 | Skipped without live PostgreSQL |
| E2E | 13 | 0 | Requires full Docker stack |
| **Total** | **643** | **53** | |

The 53 skipped tests are DB/API-dependent — they pass with a live Docker stack and valid `ANTHROPIC_API_KEY`.

**Frontend production build:**

```bash
cd ui && npm ci && npm run build
# Outputs to ui/dist/ — no build errors expected
```

---

## Project Structure

```
contragate/
├── proxy/                  MCP proxy — intercepts tool calls, async approval protocol
├── orchestrator/           LangGraph state machine, workflow store, all state handlers
├── agents/                 Three agents: Analyzer, Context+Sim, Contract
│   ├── analyzer/
│   ├── context_sim/
│   └── contract/
├── mcp_servers/            Six MCP servers, each with a bounded permission role
│   ├── postgres_reader/    SELECT-only on production DB
│   ├── transaction_sandbox/ Read-write on staging — no network egress
│   ├── memory_store/       pgvector read/write, tenant-scoped
│   ├── audit_logger/       Append-only, row-level checksums
│   ├── policy_store/       Read-only from agent pipeline
│   └── notifier/           Slack (prod) / terminal mock (local)
├── sql_analysis_lib/       Standalone analysis library — 5 modules, 30 fixtures
├── ui/                     React 18 + Vite approval UI
├── seed/                   Demo schema, historical operations, policies, sandbox triggers
├── tests/                  Unit, integration, E2E test suites
├── docs/                   ADRs, phase docs, design decisions, brand assets
├── docker-compose.yml      Seven-container local dev stack
├── docker-compose.prod.yml Railway production overrides
├── railway.json            Four-service Railway topology
├── Makefile                Common development operations
└── .env.example            Environment variable template
```

| Directory | Purpose |
|---|---|
| `proxy/` | MCP proxy entry point (port 8000), async protocol, risk gate |
| `orchestrator/` | LangGraph graph, state handlers, workflow store, handoff schema |
| `agents/` | Three specialized agents and their tools |
| `mcp_servers/` | Six permission-scoped MCP servers — all external access flows through here |
| `sql_analysis_lib/` | Deterministic SQL analysis — cascade tracing, row estimation, trigger detection, reversibility |
| `ui/` | React approval queue, contract view, audit log |
| `seed/` | Demo e-commerce schema + 20 seeded historical operations |

---

## Deployment

ContraGate is configured for [Railway](https://railway.app) deployment with four application services and two PostgreSQL instances.

**Services (`railway.json`):**

| Service | Description |
|---|---|
| `contragate-proxy-orchestrator` | Combined proxy + orchestrator. Public MCP endpoint. |
| `contragate-agents` | Three-agent service. 2 replicas. |
| `contragate-mcp` | All six MCP servers. |
| `contragate-ui` | React UI — static hosting. |

**Two PostgreSQL instances required:**
- Application database — memory store, audit log, policy store
- Staging database — transaction sandbox (separate instance; never reuse the app DB)

**Seed after first deployment:**

```bash
railway run psql $DATABASE_URL -f seed/demo_schema.sql
railway run psql $DATABASE_URL -f seed/historical_operations.sql
railway run psql $DATABASE_URL -f seed/default_policies.sql
railway run psql $STAGING_DATABASE_URL -f seed/staging_schema.sql
railway run psql $STAGING_DATABASE_URL -f seed/sandbox_trigger.sql
```

**Required environment variables for Railway:** `ANTHROPIC_API_KEY`, `DATABASE_URL`, `STAGING_DATABASE_URL`, `SLACK_BOT_TOKEN`, `SLACK_APPROVAL_CHANNEL`, `SLACK_SIGNING_SECRET`, `DEV_MODE=false`.

> **Note:** Railway deployment artifacts are complete and correct. Live Railway deployment has not been verified in this repository version — it requires a provisioned Railway account, two PostgreSQL plugins, and operator credentials. Local Docker Compose gives identical behavior to the Railway topology.

---

## Security Model

ContraGate's security properties are enforced at the code boundary, not by convention.

**Permission boundaries:**

| Boundary | Enforcement |
|---|---|
| Production database | `postgres_reader` MCP server — SELECT-only role. No agent makes a direct DB connection. |
| Staging database | `transaction_sandbox` MCP server — always wraps SQL in `BEGIN/ROLLBACK`. No network egress. |
| Memory store | `memory_store` MCP server — every query filters by `tenant_id`. Cross-tenant retrieval is structurally impossible. |
| Audit log | `audit_logger` MCP server — append-only. Row-level SHA-256 checksums on write. No `UPDATE` or `DELETE` from normal code paths. |

**Deterministic control invariants:**

| Invariant | Enforcement |
|---|---|
| Risk tier is never LLM-produced | Pure Python function — typed inputs, `RiskTier` enum output |
| Rollback SQL is never LLM-generated | Only mechanically derivable plans are produced |
| Policy rules cannot be overridden | Policy Gate runs before agents; LLM output is consumed as structured JSON |
| Raw SQL is never trusted as instruction | All SQL content wrapped in `<untrusted_input>` tags before any LLM call |

**Approval token security:**

- Single-use: `approval_id` decided exactly once (`record_decision()` enforces)
- No replay: used tokens are rejected with `409 Conflict`
- Stale-state guard: manifest hash verified before execution
- No mismatched identity: EXECUTION retrieves the original manifest, never data from the approval payload

**Process isolation:** all external system access flows through MCP servers. No agent makes arbitrary direct database connections or API calls outside the MCP protocol.

**15 security invariants** are formally specified in [CLAUDE.md §22](CLAUDE.md) and verified in the Phase 10.5 audit ([docs/PHASE_10_5_LOCAL_RELEASE_AUDIT.md](docs/PHASE_10_5_LOCAL_RELEASE_AUDIT.md)).

---

## Design Decisions

Five architecture-level decisions with direct engineering consequences:

**Why three agents, not one?**

A single LLM agent would mix tool calls (deterministic) with risk classification (must be deterministic) with prose generation (appropriate for LLM). Separating them by responsibility makes the trust boundary enforceable at the code level, enables parallel retrieval + simulation within the Context+Sim state, and produces a traceable `workflow_provenance` lineage for every contract field. See [ADR-001](docs/ADR/ADR-001-three-agents.md).

**Why deterministic risk classification?**

An LLM can be manipulated through prompt injection. If risk tier depended on LLM interpretation, a malicious operation description could argue itself to a lower tier. The rule engine is a pure Python function: given the same inputs, the same tier is always produced. See [ADR-003](docs/ADR/ADR-003-deterministic-risk.md).

**Why a writable staging instance, not a read replica?**

PostgreSQL read replicas reject write operations at the streaming replication level. A `DELETE` inside `BEGIN/ROLLBACK` still fails on a replica before reaching `ROLLBACK`. The simulation must execute the real SQL against real data structures for CASCADE rules, row counts, and trigger logic to behave correctly. See [ADR-002](docs/ADR/ADR-002-transaction-sandbox.md).

**Why async approval?**

Standard MCP clients have fixed timeouts. Holding a connection open for up to 30 minutes exhausts connection pools and triggers client timeouts. HTTP 202 with a polling URL is the correct response for "accepted but not yet processed." The SSE stream provides real-time updates for clients that support it. See [ADR-004](docs/ADR/ADR-004-async-approval.md).

**Why EXPLAIN before routing?**

The full pipeline takes 3–15 seconds. Running it on a trivial `SELECT id FROM users LIMIT 1` is wasteful. EXPLAIN returns in under 10ms and provides a reliable preliminary risk signal, resolving the circular dependency: routing requires a risk signal, but computing one requires the pipeline. See [ADR-005](docs/ADR/ADR-005-explain-gate.md).

---

## Known Limitations

These are honest scope boundaries, not defects.

**MVP scope:**

- **PostgreSQL only.** The architecture supports extension to other tool domains without redesign, but only PostgreSQL is implemented.
- **Single tenant.** The schema supports `tenant_id` on every record; multi-tenant provisioning is not built.
- **Single approver.** Dual-approval is a schema field (`dual_approval_required`) but not implemented.
- **API key authentication.** No OIDC, OAuth, or RBAC. `USE_MOCK_AUTH=true` in local dev.
- **No cross-domain consequence propagation.** Webhook consequences are traced to the boundary (URL detected) but downstream external effects are not modeled.

**Simulation boundary:**

- `pg_net`, `dblink`, and other non-transactional extensions are detected and flagged but not simulated. The sandbox records "would have fired" entries — the system shows *that* a call would fire and its parameters, not the downstream result.
- The staging instance must be writable. Physical read replicas cannot be used.

**Production:**

- Railway deployment artifacts are complete but not live-verified in this repository version (requires operator credentials).
- `pgvector` extension must be installed on Railway PostgreSQL (not included by default).
- SSE subscriber queues are in-memory only — clients must reconnect after process restart. The polling endpoint is unaffected.
- Embeddings API calls add ~200–500ms latency for first-time intent queries (results are cached by hash).

See [docs/KNOWN\_LIMITATIONS.md](docs/KNOWN_LIMITATIONS.md) for the full list.

---

## Roadmap

**Current scope (implemented):**

- PostgreSQL SQL operations via MCP tool calls
- Three-agent consequence analysis pipeline
- Transaction sandbox simulation
- Three-stage historical memory retrieval
- Deterministic risk classification
- React approval UI + Slack notifier
- PostgreSQL-backed audit log with tamper-evidence
- Async approval protocol with SSE streaming
- Railway deployment artifacts

**Planned (not implemented):**

- Additional tool domains — MySQL, file system, API call interception
- OIDC / OAuth approver authentication
- Dual-approval workflows
- Propagation Agent for cross-system consequence tracing
- Grafana / Prometheus observability integration
- Audit log export (S3, SIEM)
- Multi-tenant provisioning API

---

## Contributing

**Development setup:**

```bash
git clone https://github.com/hardik2004gupta/ContraGate.git
cd ContraGate
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
make up
```

**Testing before a PR:**

```bash
make test        # unit + integration — must pass
make test-e2e    # E2E — requires full stack
```

**Code expectations:**

- Python: type annotations on all function signatures, Pydantic v2 for structured data, `async`/`await` throughout backend code, `logging` module (no `print()` in production paths)
- The `HandoffContract` schema is authoritative — all inter-agent communication goes through it
- Risk classification must remain deterministic — no LLM output influences `RiskTier`
- New SQL operations must include a fixture in `sql_analysis_lib/tests/fixtures/`

**Commit format:** `feat(component): description` or `fix(component): description`

---

## License

> No `LICENSE` file is present in this repository. The license status is unresolved. Until a license is added, the standard default applies: no permission is granted to use, copy, modify, or distribute this software beyond reading it on GitHub.

If you are the repository owner and intend this to be open source, add a `LICENSE` file (MIT, Apache-2.0, or similar) before publishing.

---

<div align="center">

<img src="docs/brand/01-contragate-primary-symbol.png" alt="ContraGate symbol" width="48" />

**ContraGate** · *Know the consequences before the action executes.*

Built with [LangGraph](https://github.com/langchain-ai/langgraph) · [Anthropic](https://anthropic.com) · [pgvector](https://github.com/pgvector/pgvector)

</div>
