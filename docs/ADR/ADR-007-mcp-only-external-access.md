# ADR-007: All External Access Flows Through MCP Servers

**Status:** Accepted  
**Date:** 2026-08-08  
**Source:** ContraGate MVP.pdf — MCP Server Architecture

---

## Context

The three agents need to access: the production PostgreSQL database, the staging database, the vector
memory store, the audit log, the policy store, and the notifier (Slack). Two approaches:

**Option A:** Agents make direct database connections and API calls using Python database drivers and
HTTP clients directly in agent code.

**Option B:** All external access is mediated through MCP servers. Agents call MCP tools — they do not
hold database connections or HTTP clients directly.

---

## Decision

ContraGate uses **Option B: all external system access flows through MCP servers**. No agent makes
arbitrary direct database connections or API calls.

---

## Rationale

**Permission scoping:** MCP servers enforce permission boundaries that cannot be bypassed by agent
code. The `postgres-reader` server can only execute SELECT statements — there is no code path in the
agent that could accidentally issue a write to production. The `audit-logger` server enforces
append-only — there is no code path in the agent that could accidentally delete an audit record. These
guarantees are architectural, not disciplinary.

**Schema validation:** Every MCP tool call is schema-validated at the server boundary. An agent cannot
pass malformed data to a database — the MCP server validates tool inputs before executing.

**Automatic logging:** Every MCP tool call is automatically logged by the MCP protocol. This provides
complete observability of every external access without instrumentation in agent code.

**Testability:** MCP servers can be mocked or stubbed during unit testing. Agent code that holds
direct database connections requires a real database for testing.

**Scalability:** In production, each MCP server runs independently and can be scaled independently.
Direct connections from agents would require each agent instance to hold connection pool state.

---

## Consequences

- Each of the six MCP servers has a clearly defined permission boundary enforced at the server level
- Agent code contains no database connection strings, no database driver imports, no HTTP client code
- Testing agents requires only mock MCP servers, not real databases
- The `contragate-mcp` container runs all six servers for local development
- In production, each MCP server is a separate Railway service
- New external integrations (future: other databases, APIs) are added as new MCP servers, not as
  direct dependencies in agent code
