# ContraGate

**Know the consequences before the action executes.**

ContraGate is an agentic workflows layer that transparently intercepts MCP tool-calling agents, runs a
three-agent pre-execution audit covering blast radius analysis, reversibility classification, three-stage
memory retrieval, and transaction-sandboxed simulation, and presents humans with risk-tiered consequence
contracts before any action executes against production.

## Status

**Phase 0 complete.** Repository foundation and engineering contract established. Implementation has not
begun. See [CLAUDE.md](CLAUDE.md) for the full engineering specification.

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

## Quick Start (when implemented)

```bash
git clone <repo>
cd contragate
cp .env.example .env
# Add ANTHROPIC_API_KEY to .env
docker compose up
# Wait ~45 seconds for health checks
open http://localhost:3000
```

## Documentation

- **[CLAUDE.md](CLAUDE.md)** — Complete engineering contract (authoritative specification)
- **[docs/IMPLEMENTATION_PLAN.md](docs/IMPLEMENTATION_PLAN.md)** — Phase-by-phase build plan
- **[docs/ADR/](docs/ADR/)** — Architecture Decision Records

## Source Documents

The authoritative specification is:
1. `ContraGate_Complete_Architecture_and_Repository_Plan.pdf`
2. `ContraGate MVP.pdf`

## Known Limitations (Honest Scope)

- **Single domain:** The MVP covers PostgreSQL SQL operations only.
- **Physical read replica exclusion:** The transaction sandbox requires a writable staging instance.
- **Non-transactional extension simulation:** pg_net / dblink calls are detected and flagged, not fully simulated.
- **Single approver:** Dual-approval is a schema field but not implemented in the MVP.
- **No OIDC:** API key authentication only.
- **No cross-domain consequence propagation:** Webhook consequences are traced to the boundary only.
