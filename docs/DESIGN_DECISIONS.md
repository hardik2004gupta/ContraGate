# ContraGate — Design Decisions

Five architecture-level decisions that shaped ContraGate's design. These are the decisions
that distinguish this system from simpler alternatives and deserve explanation in any technical
review.

---

## Decision 1: Three Agents, Not One

**Alternative considered:** A single monolithic LLM agent that analyzes, retrieves context, simulates,
and writes the contract.

**Decision:** Three specialized agents — Analyzer, Context+Simulation, Contract — each with
distinct responsibilities and tools.

**Reasoning:**
- **Separation of concerns at the trust boundary.** The Analyzer runs deterministic code
  (sql_analysis_lib) wrapped by thin LLM-structured-output calls. The Context+Sim agent
  runs I/O-bound retrieval and sandbox execution. The Contract agent does LLM prose summarization
  of already-computed structured fields. Mixing these would allow LLM discretion in the wrong
  places.
- **Parallel execution.** The Context+Sim agent runs retrieval and sandbox simulation in parallel
  within its state. This is only possible because context retrieval and simulation have no mutual
  dependency. A single agent serializes this unnecessarily.
- **Auditability.** Each agent appends to `workflow_provenance` with the exact fields it wrote.
  The lineage of every contract field is traceable to the specific agent and timestamp. A single
  agent makes this uninterpretable.
- **Risk isolation.** The architecture prohibits LLM-generated risk classification and rollback
  SQL. Having a dedicated deterministic Contract agent makes this invariant enforceable at the
  code boundary, not just by convention.

**See:** `ADR-001-three-agents.md`

---

## Decision 2: Transaction Sandbox, Not Read Replica

**Alternative considered:** Run the simulation query against a physical PostgreSQL read replica
behind a `ROLLBACK`-planned transaction.

**Decision:** Run simulation against a writable staging instance — never against a read replica.

**Reasoning:**
- Read replicas reject write operations at the streaming replication level. Even inside a
  `BEGIN`/`ROLLBACK` block, a `DELETE` or `UPDATE` statement fails before reaching `ROLLBACK`
  because the replica does not accept writes. The simulation would fail for the exact queries
  that need simulation most.
- A staging instance with the same schema and synthetic data provides faithful simulation: the
  database processes the query against real data structures, CASCADE rules fire correctly, row
  counts reflect actual data distributions, and trigger logic executes (in sandbox mode).
- The sandbox mode flag (`app.sandbox_mode = 'true'`) causes application-level triggers to log
  their intended external calls to `sandbox_trigger_log` instead of executing them. This is
  the honest simulation primitive: database behavior is captured; external side effects are
  "would have fired" entries.

**See:** `ADR-002-transaction-sandbox.md`

---

## Decision 3: Deterministic Risk Classification

**Alternative considered:** Let the LLM classify risk tier based on the contract fields it
has already computed.

**Decision:** Risk tier classification is a pure deterministic function of structured inputs.
No LLM output influences the risk tier.

**Reasoning:**
- An LLM can be manipulated through prompt injection. If the risk tier depended on LLM
  interpretation, a malicious operation description could argue itself to a lower tier.
- Deterministic classification is reproducible and auditable. Given the same inputs, the
  same tier is always produced. This satisfies compliance requirements.
- The rule set (PERMANENT → FULL_CONTRACT, historical rejection → FULL_CONTRACT, etc.) is
  directly derivable from the architecture spec and covers the known risk vectors. Cases
  genuinely outside the rule set are surfaced with explicit `uncertainty_score` flags, not
  silently resolved by LLM discretion.
- LLM-generated rollback SQL is explicitly prohibited. The rollback plan must be
  mechanically derivable from schema metadata or it does not exist. This prevents a class
  of failure where the LLM generates plausible-but-wrong recovery SQL.

**See:** `ADR-003-deterministic-risk.md`

---

## Decision 4: Async Approval Protocol

**Alternative considered:** Hold the MCP connection open while the human reviews the contract
(synchronous approval).

**Decision:** The proxy immediately returns `PENDING_HUMAN_APPROVAL` (HTTP 202) with a polling
URL and SSE stream URL. The calling agent polls for the terminal state.

**Reasoning:**
- Standard MCP clients implement a request-response protocol with a fixed timeout. Holding
  a connection open for 30 minutes (the review timeout) would exhaust connection pools and
  trigger timeouts in every MCP client implementation tested.
- HTTP 202 Accepted is the semantically correct response for "accepted but not yet processed."
  The `approval_id` in the response gives the caller a handle to check status.
- SSE (`GET /v1/approvals/{id}/stream`) provides real-time updates without polling overhead
  for clients that support it. The polling fallback serves clients that don't.
- The original tool-call manifest is stored keyed by `approval_id`. On approval, the
  EXECUTION state retrieves the manifest and executes it — no data is trusted from the
  approval payload itself. This eliminates a class of approval-then-swap attacks.

**See:** `ADR-004-async-approval.md`

---

## Decision 5: EXPLAIN Before Routing

**Alternative considered:** Route every operation through the full three-agent pipeline
and apply risk classification at the end.

**Decision:** Run `EXPLAIN (FORMAT JSON)` via the postgres-reader MCP server as the first step
of RISK_GATE, before any agent runs, before the Policy Gate.

**Reasoning:**
- The full pipeline (three agents + retrieval + simulation) takes 3–15 seconds. Applying it
  to a trivial `SELECT id FROM users LIMIT 1` is wasteful and degrades latency for the fast
  path.
- EXPLAIN returns in under 10 milliseconds on a live database. The cost estimate and scan
  type provide a reliable preliminary risk signal: cost > 50,000 or PII table involvement
  triggers standard review minimum without running the pipeline.
- The Read Impact Gate resolves the circular dependency: fast-path routing requires a risk
  signal, but computing the full risk score requires the pipeline. EXPLAIN provides the
  signal cheaply and correctly.
- EXPLAIN runs for ALL operations including writes (it does not execute the write). This is
  important: a bulk `DELETE` that would have passed the fast-path threshold is caught before
  wasting pipeline resources.

**See:** `ADR-005-explain-gate.md`
