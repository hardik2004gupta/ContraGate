# ADR-002: Transaction Sandbox Instead of Docker Clone

**Status:** Accepted  
**Date:** 2026-08-08  
**Source:** ContraGate MVP.pdf — Design Decision Reference

---

## Context

To simulate a SQL operation before execution, the system needs a safe environment where the operation
can run against realistic data without affecting production. Two approaches were considered:

**Option A:** Docker PostgreSQL clone — spin up a container initialized from a schema dump and seeded
with synthetic data.

**Option B:** Transaction sandbox — run `BEGIN` / execute / capture differences / `ROLLBACK` inside an
explicit transaction on a writable staging instance with real data.

---

## Decision

ContraGate uses **Option B: transaction sandbox** against a writable staging instance with real production-mirrored data.

---

## Rationale

A Docker PostgreSQL clone initialized from a schema dump and seeded with synthetic data **produces synthetic
row counts**. If the clone has 50 rows and production has 47,000, the sandbox row diff is fiction. The
purpose of simulation — to give the human accurate row counts — is defeated.

The transaction sandbox uses `BEGIN`/`ROLLBACK` against a real staging instance with production-mirrored
data, producing **real row counts from real data**. The tradeoffs versus the Docker clone approach:

| Factor | Docker Clone | Transaction Sandbox |
|--------|-------------|---------------------|
| Row count accuracy | Synthetic (fictional) | Real |
| Setup overhead | Container spin-up, seed, teardown (30–60 seconds) | Near-zero (transaction open/close) |
| Operational dependency | Docker on agent machine | Writable staging instance |
| Rollback guarantee | Container teardown | PostgreSQL ROLLBACK (atomic) |
| Execution time | 30–60 seconds | Under 1 second |

The Docker clone approach eliminates Docker orchestration overhead but the total sandbox execution time
drops from 30–60 seconds to under one second with the transaction approach.

**Known limitation (documented honestly):** The system requires a writable staging instance. PostgreSQL
physical read replicas reject write statements even inside rolled-back transactions. Projects running
only physical read replicas cannot use the sandbox without provisioning a staging instance. This is
documented in `CLAUDE.md` Known Limitations.

---

## Consequences

- The `transaction_sandbox` MCP server must connect to a **writable staging instance** — never production, never a physical read replica
- `SET LOCAL app.sandbox_mode = 'true'` must be set before every SQL execution in the sandbox
- `SET LOCAL statement_timeout = '5000ms'` must be set to prevent runaway simulation queries
- Application-level triggers must be installed on the staging database that respect the sandbox_mode flag
- The `seed/staging_schema.sql` and `seed/sandbox_trigger.sql` scripts initialize the staging instance correctly
- Local development uses a separate PostgreSQL schema (not a separate instance) for the staging database
- Railway production uses two separate PostgreSQL instances
