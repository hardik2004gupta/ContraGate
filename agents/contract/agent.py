"""
Contract Agent — orchestration.

PHASE 0 SCAFFOLD — NOT IMPLEMENTED.
Implementation begins in Phase 5.

Responsibilities (CLAUDE.md §8 — Agent 3):
- Assemble all agent outputs into human-readable consequence contract
- Apply deterministic risk tier classification (risk_rules.py — NO LLM)
- Use LLM for natural-language summarization ONLY

LLM call scope:
- Takes structured fields from the HandoffContract
- Produces readable prose for each of the four contract sections
- The LLM receives the already-computed risk_tier — it cannot change it

CRITICAL (CLAUDE.md §22, invariants 2, 3):
- Risk tier classification must be deterministic and reproducible
- LLM output is consumed as natural-language text only
- LLM cannot produce, adjust, or override the risk_tier field
"""
