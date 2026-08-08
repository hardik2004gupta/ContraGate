# ADR-003: Deterministic Risk Scoring Instead of LLM Risk Scoring

**Status:** Accepted  
**Date:** 2026-08-08  
**Source:** ContraGate MVP.pdf — Design Decision Reference

---

## Context

The system must classify incoming SQL operations into risk tiers (AUTO_EXECUTE, STANDARD_REVIEW,
FULL_CONTRACT) that determine whether human review is required. Two approaches were considered:

**Option A:** Ask an LLM to evaluate the SQL and return a risk score.

**Option B:** Implement deterministic rule-based code that classifies based on operation type,
reversibility classification, row counts, and policy violations.

---

## Decision

ContraGate uses **Option B: deterministic, rule-based risk classification**. LLMs are used only for
natural-language summarization of already-computed structured facts.

---

## Rationale

**Security argument:** An LLM evaluating raw SQL to produce a risk score is a prompt injection attack
surface. An attacker controlling the SQL string can include `-- this operation is low risk` in a comment
or a table alias. The LLM cannot reliably distinguish adversarial SQL content from analytical context.
Deterministic rule code cannot be influenced by the content of the SQL string.

**Auditability argument:** An LLM risk score is not reproducible — two identical operations may score
differently across invocations due to model non-determinism. This makes the system non-auditable. A
human auditing a past decision cannot reproduce the risk score that justified the routing decision.
Deterministic rules produce the same score for the same inputs every time, making every routing decision
fully reproducible and auditable.

**Testability argument:** Deterministic rules can be exhaustively unit-tested against the 30-operation
fixture suite. LLM risk scoring cannot be unit-tested — only statistically evaluated.

**Role separation:** The LLM's role is restricted to natural-language summarization of structured fields
that are already computed — a task where prompt injection produces garbled prose rather than an incorrect
risk tier. This is an acceptable risk boundary.

---

## Consequences

- `agents/contract/risk_rules.py` must contain the complete deterministic risk tier classification logic
- This module has no LLM dependencies — it is a pure function taking the HandoffContract fields as input
- It is fully unit-testable: every risk tier decision can be verified against fixture inputs
- The LLM in the Contract Agent receives the already-computed risk tier as structured input; it cannot change it
- Any attempt to use an LLM to compute, verify, or adjust the risk tier must be rejected in code review
