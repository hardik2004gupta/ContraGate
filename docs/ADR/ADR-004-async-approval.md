# ADR-004: Asynchronous Approval Protocol Instead of Blocking MCP

**Status:** Accepted  
**Date:** 2026-08-08  
**Source:** ContraGate MVP.pdf — Design Decision Reference

---

## Context

ContraGate intercepts MCP tool calls and may require a human to review and approve an operation before
execution. The review process takes minutes to hours. The calling agent is connected via a standard
MCP JSON-RPC socket.

Two approaches were considered:

**Option A:** Hold the MCP connection open while waiting for human review, returning the result
synchronously when the human decides.

**Option B:** Return immediately with a `PENDING_HUMAN_APPROVAL` token and provide polling/SSE
endpoints for the calling agent to monitor the decision.

---

## Decision

ContraGate uses **Option B: asynchronous approval protocol**. The proxy returns a pending token
immediately and never holds the calling agent's connection open.

---

## Rationale

Standard MCP JSON-RPC clients have connection timeouts measured in seconds to minutes. A human approval
workflow measured in minutes to hours cannot happen inside a synchronous connection without causing
connection errors on the calling agent.

**This is not a workaround — it is the correct architecture** for any human-in-the-loop system where
the human decision latency is unbounded.

The async protocol:
1. Returns immediately — the calling agent's connection is not blocked
2. Provides a poll URL for status checking
3. Provides an SSE endpoint for real-time updates
4. Returns the execution result on the next status poll after approval and execution complete

The calling agent can continue other work while waiting, or can poll/stream at its own cadence. The
agent's execution continues without socket errors in either approval or rejection case.

---

## Protocol Details

**Immediate response (when review required):**
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

**Poll response after approval:**
```json
{
  "status": "APPROVED",
  "approval_id": "cg_7f3a9b12",
  "result": { ... }  // actual MCP tool execution result
}
```

---

## Consequences

- `proxy/async_protocol.py` implements the pending token generation, storage, and polling endpoints
- The original tool-call manifest must be persisted keyed by `approval_id` — it is needed for execution
- The `proxy/main.py` must expose `GET /v1/approvals/{id}/status` and `GET /v1/approvals/{id}/stream`
- Idempotency is critical: the EXECUTION state must check `execution_completed` before executing
- The complete application extends this pattern to all supported tool domains, not just PostgreSQL
