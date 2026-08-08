# ContraGate

**Know the consequences before the action executes.**

ContraGate is an agentic workflows layer that transparently intercepts MCP tool-calling agents, runs a
three-agent pre-execution audit covering blast radius analysis, reversibility classification, three-stage
memory retrieval, and transaction-sandboxed simulation, and presents humans with risk-tiered consequence
contracts before any action executes against production.

## Status

**Phase 8 complete.** Production hardening applied. Phases 1–8 implemented.

| Phase | Description | Status |
|-------|-------------|--------|
| Phase 0 | Architecture + repository foundation | ✅ Complete |
| Phase 1 | `sql_analysis_lib` — 5 modules, 30 fixtures, all tests passing | ✅ Complete |
| Phase 2 | Docker Compose + MCP servers + LangGraph skeleton + async protocol | ✅ Complete |
| Phase 3 | Analyzer Agent — blast radius, reversibility, prompt injection boundary | ✅ Complete |
| Phase 4 | Context + Simulation Agent — 3-stage retrieval + transaction sandbox | ✅ Complete |
| Phase 5 | Contract Agent — deterministic risk rules + LLM summarization | ✅ Complete |
| Phase 6 | Full orchestration — all state transitions, guards, timeouts, retries | ✅ Complete |
| Phase 7 | React UI + decision loop — approval queue, contract view, audit log | ✅ Complete |
| Phase 8 | Production hardening — PostgreSQL persistence, security fixes, cleanup | ✅ Complete |
| Phase 9 | Integration + Railway deployment | 🔜 Next |
| Phase 10 | Hardening + final verification + screen recording | 🔜 Upcoming |

## Architecture

```
External Agent (MCP Client)
    → ContraGate MCP Proxy (port 8000)
    → Risk Gate (EXPLAIN + Policy)
    → LangGraph Orchestrator
    → Analyzer Agent
    → Context + Simulation Agent (parallel retrieval + sandbox)
    → Contract Agent
    → Human Review (React UI / Slack)
    → Execution Relay
    → Audit + Feedback Loop
```

## Quick Start

```bash
git clone <repo>
cd contragate
cp .env.example .env
# Add ANTHROPIC_API_KEY to .env
docker compose up
# Wait ~45 seconds for health checks
open http://localhost:3000
```

## Test Suite

```bash
# Unit + integration tests (no external services needed)
pytest sql_analysis_lib/tests/ tests/ -m "not e2e"

# E2E tests (requires running Docker stack)
docker compose up -d
pytest tests/e2e/ -v -m e2e
```

Current baseline: **628 passed, 53 skipped** (skipped tests require live PostgreSQL via Docker).

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — Complete engineering contract (authoritative specification)
- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — Phase-by-phase build plan
- **[docs/ADR/](docs/ADR/)** — Architecture Decision Records
- **[docs/PHASE_8_PRODUCTION_HARDENING.md](docs/PHASE_8_PRODUCTION_HARDENING.md)** — Phase 8 gap audit and changes

## Known Limitations (Honest Scope)

- **Single domain:** The MVP covers PostgreSQL SQL operations only.
- **Physical read replica exclusion:** The transaction sandbox requires a writable staging instance, never a read replica.
- **Non-transactional extension simulation:** pg_net / dblink calls are detected and flagged but not fully simulated.
- **Single approver:** Dual-approval is a schema field but not implemented in the MVP.
- **No OIDC:** API key authentication only (`USE_MOCK_AUTH=true` in local dev).
- **No cross-domain consequence propagation:** Webhook consequences are traced to the boundary only.
- **53 PostgreSQL-dependent tests:** Skipped without a live PostgreSQL instance (start Docker stack to run them).

## Design Decisions

**Why three agents?**
The Analyzer, Context+Simulation, and Contract agents are separated by responsibility boundary, not by
convenience. The Analyzer is a pure tool-caller over deterministic code. The Context+Sim agent runs
parallel I/O (retrieval + simulation). The Contract agent applies deterministic risk rules and then
calls the LLM for natural language summarization. Collapsing them would mix LLM calls with tool calls
and make risk classification harder to audit. See [ADR-001](docs/ADR/ADR-001-three-agents.md).

**Why is risk classification never LLM-produced?**
An LLM can be prompt-injected. A deterministic rule engine cannot. If the risk tier could be overridden
by clever SQL content, the entire consequence gate becomes meaningless. The rule engine is a pure Python
function that takes typed inputs and returns a `RiskTier` enum. It has 100% unit test coverage.
See [ADR-003](docs/ADR/ADR-003-deterministic-risk.md).

**Why a transaction sandbox instead of a read replica?**
Physical read replicas in PostgreSQL reject all writes, including writes inside a `BEGIN...ROLLBACK`
block. The sandbox needs to execute the real SQL, capture row diffs, and record trigger behavior.
This requires a writable staging instance with the same schema and sandbox-aware triggers.
See [ADR-002](docs/ADR/ADR-002-transaction-sandbox.md).

**Why PostgreSQL for workflow persistence?**
The system already has a PostgreSQL instance (required for pgvector, audit log, policy store).
Adding a separate Redis dependency for the workflow store would increase infrastructure complexity
without benefit. PostgreSQL JSONB storage of `HandoffContract` gives us queryability for future
analytics without any additional service. See workflow_store.py.

**Why does EXPLAIN run before routing, not after?**
To resolve the circular dependency: fast-path routing requires a risk signal, but computing a risk
signal requires the full pipeline. EXPLAIN provides a 10ms preliminary signal that breaks the cycle.
A 50,000 cost unit threshold maps to roughly 10,000 rows in a typical e-commerce schema.
See [ADR-005](docs/ADR/ADR-005-explain-gate.md).
