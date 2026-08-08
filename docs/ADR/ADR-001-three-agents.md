# ADR-001: Three Agents Instead of Six

**Status:** Accepted  
**Date:** 2026-08-08  
**Source:** ContraGate MVP.pdf — Design Decision Reference

---

## Context

Early design sketches considered a more granular pipeline with six specialized agents:
1. SQL Parser Agent
2. Schema Validator Agent
3. Blast Radius Agent
4. Reversibility Agent
5. Memory Retrieval Agent
6. Contract Assembly Agent

The MVP specification explicitly consolidated these into three agents.

---

## Decision

ContraGate uses exactly **three** primary analytical agents:

1. **Analyzer Agent** — SQL parsing, schema validation, blast radius, reversibility, external side-effect detection
2. **Context and Simulation Agent** — Historical retrieval + transaction sandbox execution (parallel)
3. **Contract Agent** — Contract assembly + deterministic risk tier classification + LLM summarization

---

## Rationale

Six agents with thin, overlapping responsibilities create:
- Unnecessary inter-agent context serialization — the same schema metadata must be passed multiple times
- Additional LLM API calls for tasks that are logically part of the same reasoning cluster
- Coordination overhead that increases latency without adding analytical capability

The three consolidated agents each handle a **coherent cluster of responsibilities that naturally belong together**.

The Analyzer Agent's responsibilities (intent parsing, schema validation, blast radius, reversibility) all require
the same schema metadata context and produce outputs that are consumed together. Splitting them would require
either passing that context multiple times or building a shared context store — both worse than consolidation.

The Context and Simulation Agent's two core tasks (retrieval and simulation) run in **parallel within the agent**
to minimize latency. Splitting them into separate agents would force a serial dependency or require a complex
parallel coordination mechanism external to either agent.

The Contract Agent's responsibilities (assembling structured data and generating readable prose) are tightly
coupled — the LLM summarization requires all structured fields to already be present in the contract before
any prose can be generated.

---

## Consequences

- Phase 3 implements the Analyzer Agent completely before Phase 4 begins
- Phase 4 implements the Context and Simulation Agent (both retrieval and simulation sub-tasks)
- Phase 5 implements the Contract Agent
- The sql_analysis_lib (Phase 1) is the prerequisite for Phase 3 — it keeps the Analyzer Agent thin
- All three agents are stateless per invocation — all state flows through the typed HandoffContract
