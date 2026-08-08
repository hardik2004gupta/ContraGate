"""
Contract section assembly.

PHASE 0 SCAFFOLD — NOT IMPLEMENTED.
Implementation begins in Phase 5.

Assembles the four-section consequence contract (CLAUDE.md §17):

Section 1 — What will happen?
  compile_what_will_happen(contract) → structured data for UI rendering
  - Operation intent (LLM-generated prose from intent_summary)
  - Primary impact: table, row counts (estimated | actual from staging), condition
  - Cascade impact: dependent tables with row counts and cascade type
  - Total rows across all tables
  - Estimated duration
  - External actions that will fire (from simulation.external_calls_logged)

Section 2 — What cannot be undone?
  compile_what_cannot_be_undone(contract) → structured data for UI rendering
  - Reversibility classification badge (PERMANENT / PARTIAL / REVERSIBLE)
  - Database changes explanation
  - External effects explanation
  - For PERMANENT: acknowledgement requirement flag = True

Section 3 — Has this happened before?
  compile_historical_precedents(contract) → structured data for UI rendering
  - Top 3 from retrieval_results.top3_historical
  - Ranked by outcome severity (REJECTED shown first)

Section 4 — What does the system flag?
  compile_system_flags(contract) → structured data for UI rendering
  - Policy violations from policy_violations
  - Historical rejection warnings
  - Simulation unavailability warning (if simulation_available == False)
  - Blast radius confidence with reason

generate_rollback_plan(contract) -> str | None
  Derives rollback procedure from reversibility classification.
  MECHANICAL DERIVATION ONLY — never calls an LLM.
  Returns None for PERMANENT operations.
"""
