# ADR-006: SQL Analysis Library Is a Standalone Prerequisite

**Status:** Accepted  
**Date:** 2026-08-08  
**Source:** ContraGate MVP.pdf — Design Decision Reference (Week Zero)

---

## Context

The Analyzer Agent requires several complex SQL analysis capabilities: FK cascade tracing, row
estimation, trigger detection, reversibility classification, and EXPLAIN parsing. These could be:

**Option A:** Implemented directly inside the Analyzer Agent alongside its orchestration logic.

**Option B:** Implemented as a standalone, dependency-free library (`sql_analysis_lib`) that the
Analyzer Agent's tools call as thin wrappers.

---

## Decision

ContraGate builds `sql_analysis_lib` as a **standalone prerequisite** (Phase 1) that must be fully
implemented and tested before any agent code begins (Phase 2+).

---

## Rationale

**Testing argument:** SQL analysis logic embedded inside an agent is difficult to test. It requires a
running LangGraph instance, a running MCP server, and an active orchestration context. As a standalone
library, each module is a pure function that can be unit-tested with a direct PostgreSQL connection and
a fixture file — no agent infrastructure needed.

**Dependency ordering:** Week Two of the six-week build plan implements the Analyzer Agent. The MVP
explicitly states: "Building it independently makes Week 2 an integration sprint rather than a full
implementation sprint." If sql_analysis_lib is built in parallel with the agent, Week 2 would need to
implement both the library logic and the agent orchestration simultaneously, which creates unresolvable
dependencies.

**Separation of concerns:** The library contains deterministic, side-effect-free logic. The agent
contains orchestration, MCP tool invocation, and LLM structured output prompting. These are separate
concerns that should be separately testable and separately deployable.

**The 30-fixture requirement:** The library must pass 30 SQL operation fixture tests before Phase 2
begins. This fixture suite cannot be satisfied without a standalone, directly-callable library.

---

## Consequences

- `sql_analysis_lib/` is a Python package with no LangGraph or MCP dependencies
- Its only external dependency is a PostgreSQL connection (psycopg2 or asyncpg)
- `tests/fixtures/` must contain exactly 30 JSON fixture files before Phase 2 starts
- All fixture tests must pass before the Phase 1 → Phase 2 transition is approved
- The Analyzer Agent's `tools.py` contains thin wrapper functions that call into `sql_analysis_lib`
- Any SQL analysis logic found directly in `agents/analyzer/agent.py` (not in tools.py wrappers) is a violation
