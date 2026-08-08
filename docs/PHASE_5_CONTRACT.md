# Phase 5 Report — Contract Agent + Full Three-Agent Pipeline

**Status: READY**
**Date completed:** 2026-08-08
**Phase boundary:** Analyzer → HandoffContract → Context + Simulation → enriched HandoffContract → Contract Agent → validated consequence contract JSON → PHASE 6 INPUT

---

## 1. What Phase 5 Delivers

Phase 5 implements the Contract Agent — the third and final agent in the ContraGate analysis pipeline. After Phase 5, the pipeline is fully functional from tool-call interception through consequence contract production.

**New in Phase 5:**
- `agents/contract/risk_rules.py` — pure deterministic risk tier classification
- `agents/contract/contract_schema.py` — four-section ApprovalContract Pydantic schema
- `agents/contract/contract_builder.py` — deterministic contract assembly (all four sections)
- `agents/contract/prompts.py` — LLM boundary: structured-output tool definitions and prompt builder
- `agents/contract/agent.py` — ContractAgent orchestration with validated LLM prose and fallback
- `orchestrator/states/contract.py` — updated to delegate to real ContractAgent
- `orchestrator/handoff_schema.py` — added `approval_contract_json` field

**Tests added:**
- `tests/unit/test_risk_rules.py` — 41 tests
- `tests/unit/test_contract_builder.py` — 38 tests
- `tests/unit/test_contract_agent.py` — 17 tests
- `tests/integration/test_contract_assembly.py` — 24 tests

---

## 2. Architecture Conformance

### Three-Agent Model (CLAUDE.md §8)

| Requirement | Status |
|-------------|--------|
| Agent 3 runs after CONTEXT_AND_SIM state | SATISFIED — `run_contract()` in `orchestrator/states/contract.py` is invoked by the CONTRACT state |
| Risk tier via deterministic rule engine | SATISFIED — `classify_risk()` in `risk_rules.py` is pure code, no LLM |
| LLM usage: natural language summarization ONLY | SATISFIED — LLM receives structured facts, produces prose; tier/score never come from LLM |
| All agent writes append to workflow_provenance | SATISFIED — `agent.py` appends provenance record on every run |
| Contract sections: four required sections | SATISFIED — `WhatWillHappen`, `WhatCannotBeUndone`, `HasThisHappenedBefore`, `SystemFlags` |

### Risk Model (CLAUDE.md §13)

| Tier | Condition | Implemented in |
|------|-----------|----------------|
| AUTO_EXECUTE | SELECT, low cost, no violations, no PII | `_apply_tier_rules()` — AUTO branch |
| STANDARD_REVIEW | Reversible write, score 0.3–0.7, no historical rejection | `_apply_tier_rules()` — STANDARD branch |
| FULL_CONTRACT | 8 conditions in explicit priority order | `_apply_tier_rules()` — FULL branch |

**All 8 FULL_CONTRACT conditions implemented (priority order):**
1. Policy store requires FULL (`required_tier_from_policy == "FULL_CONTRACT"`)
2. PERMANENT reversibility
3. Any historical precedent with outcome == "REJECTED"
4. `prompt_injection_risk == True`
5. Cascade total rows > `CASCADE_ROW_THRESHOLD` (50,000)
6. External trigger count ≥ `EXTERNAL_TRIGGER_THRESHOLD` (5)
7. Risk score > `HIGH_RISK_SCORE` (0.7)
8. Row confidence < `LOW_CONFIDENCE_THRESHOLD` (0.25)

### Reversibility Classification (CLAUDE.md §13)

Reversibility is determined by the Analyzer Agent (Phase 3) and written to the HandoffContract. The Contract Agent reads the classification and:
- `PERMANENT` → rollback_plan = None; requires_permanent_acknowledgement = True
- `REVERSIBLE_AUTOMATED` → rollback_plan = snapshot restoration description
- `REVERSIBLE_PITR` → rollback_plan = PITR/DBA procedure description
- `PARTIAL` → rollback_plan = partial recovery description

**ABSOLUTE PROHIBITION satisfied:** No LLM is invoked to produce rollback SQL. `derive_rollback_plan()` is a pure function with no network calls.

### Security Invariants (CLAUDE.md §22)

| Invariant | Verification |
|-----------|-------------|
| #2 No LLM-controlled risk classification | `classify_risk()` is a pure function; ContractAgent writes `risk_tier` before calling LLM |
| #3 No LLM-generated rollback SQL | `derive_rollback_plan()` returns human-readable procedure text, never SQL |
| #11 Raw SQL treated as data | `build_contract_summary_prompt()` wraps `raw_sql` in `<untrusted_input>` tags |
| #12 External input is untrusted | `intent_summary` and `decision_reason` fields wrapped in `<untrusted_input>` tags in prompts |
| #13 Prompt injection cannot override deterministic controls | Risk tier set before LLM call; LLM output validated by `_validate_and_sanitize_prose()` before acceptance |
| #14 Every important action has provenance | `workflow_provenance` appended on every ContractAgent run |

---

## 3. LLM Boundary

### What the LLM does

The LLM receives a structured prompt listing already-computed facts (row counts, reversibility, historical outcomes). It produces four prose fields via `tool_use` structured output:
- `operation_summary` — plain-language description of what the operation does
- `database_changes_explanation` — what happens to the database
- `external_effects_explanation` — external side effects (if any)
- `historical_summary` — brief context from historical precedents

### What the LLM does NOT do

- Does NOT assign risk_tier (computed before LLM call)
- Does NOT classify reversibility (from Analyzer Agent)
- Does NOT determine row counts (from sql_analysis_lib)
- Does NOT generate rollback SQL (mechanically derived)
- Does NOT approve or reject operations
- Does NOT invent historical precedents (passed as structured data)

### Prompt injection defense

`build_contract_summary_prompt()` in `prompts.py`:
- Wraps `raw_sql` in `<untrusted_input>` tags
- Wraps `intent_summary` in `<untrusted_input>` tags
- Wraps each historical `decision_reason` in `<untrusted_input>` tags
- Explicitly instructs the model not to follow any instructions found inside these tags

`_validate_and_sanitize_prose()` in `agent.py`:
- Rejects LLM output containing SQL keywords (`DELETE FROM`, `UPDATE SET`, `DROP TABLE`, etc.)
- Rejects LLM output containing decision patterns (`THIS OPERATION SHOULD BE APPROVED`, etc.)
- Caps all prose fields at 600 characters
- On rejection: increments retry counter; after `_MAX_LLM_RETRIES` (2), uses `_fallback_prose()`

### Fallback prose

`_fallback_prose()` builds deterministic prose from HandoffContract structured fields only. No LLM call. The contract is always assembled — LLM unavailability degrades prose quality, not pipeline correctness.

---

## 4. Contract Schema

```
ApprovalContract
├── approval_id                     str
├── risk_tier                       str  (AUTO | STANDARD | FULL_CONTRACT)
├── risk_score                      float (0.0–1.0, 3 decimal places)
├── risk_tier_deterministic         bool = True  (schema invariant — always True)
├── requires_permanent_acknowledgement  bool
├── decision_options                list[str]
├── reason_required                 bool = True
├── reason_minimum_length           int = 10
├── timeout_at                      str  (ISO 8601 — 30 minutes from now)
├── assembled_at                    str  (ISO 8601)
└── sections
    ├── what_will_happen            (Section 1)
    │   ├── operation_summary       str  (LLM prose)
    │   ├── operation_type          str
    │   ├── primary_table           str
    │   ├── source_condition        str
    │   ├── estimated_primary_rows  int
    │   ├── actual_primary_rows     int | None
    │   ├── simulation_row_delta_note  str | None
    │   ├── cascade_impact          list[CascadeImpact]
    │   ├── total_estimated_rows    int
    │   └── external_actions        list[ExternalActionEntry]
    ├── what_cannot_be_undone       (Section 2)
    │   ├── reversibility           str
    │   ├── reversibility_reason    str
    │   ├── database_changes_explanation  str  (LLM prose)
    │   ├── external_effects_explanation  str  (LLM prose)
    │   ├── permanent_components    list[str]
    │   ├── rollback_plan           str | None  (mechanical — never LLM SQL)
    │   └── requires_permanent_acknowledgement  bool
    ├── has_this_happened_before    (Section 3)
    │   ├── retrieval_available     bool
    │   ├── precedents              list[HistoricalPrecedentEntry]  (max 3)
    │   ├── historical_summary      str  (LLM prose)
    │   └── contains_rejected_outcome  bool
    └── system_flags                (Section 4)
        ├── policy_violations       list[PolicyViolationEntry]
        ├── historical_rejection_warning  bool
        ├── simulation_unavailable  bool
        ├── retrieval_unavailable   bool
        ├── prompt_injection_risk   bool
        ├── external_actions_cannot_be_simulated  bool
        ├── confidence_reduced      bool
        └── sequence_gap_warning    bool
```

---

## 5. Demo Scenarios

### Scenario 2 (Standard UPDATE)
```
Input:  UPDATE users SET status = 'inactive' WHERE last_active < NOW() - INTERVAL '1 year'
        3,847 rows, REVERSIBLE_PITR, no historical rejections

Output: risk_tier = STANDARD
        risk_score ≈ 0.3xx
        rollback_plan = "PITR recovery: ..." (no SQL)
        requires_permanent_acknowledgement = False
```

### Scenario 3 (FULL_CONTRACT DELETE)
```
Input:  DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'
        PERMANENT, cascade > 50,000 rows, cg_1847 surfaces as REJECTED

Output: risk_tier = FULL_CONTRACT
        risk_score > 0.7
        rollback_plan = None
        requires_permanent_acknowledgement = True
        sections.system_flags.historical_rejection_warning = True
        sections.has_this_happened_before.contains_rejected_outcome = True
```

---

## 6. Test Coverage

| File | Tests | What it verifies |
|------|-------|-----------------|
| `test_risk_rules.py` | 41 | All 8 FULL conditions, STANDARD conditions, AUTO conditions, risk score components, idempotency, priority order |
| `test_contract_builder.py` | 38 | All four section builders, schema validation, rollback derivation, historical precedent handling |
| `test_contract_agent.py` | 17 | Deterministic tier before LLM, rollback mechanical, prose validation, injection defense, provenance |
| `test_contract_assembly.py` | 24 | All four sections populated, demo scenarios, LLM failure fallback, schema roundtrip |

**Total new tests: 120**

---

## 7. Known Deviations and Decisions

### `approval_contract_json` added to HandoffContract

The CLAUDE.md §11 HandoffContract JSON example does not include `approval_contract_json`. The Python schema in `orchestrator/handoff_schema.py` has always contained additional fields beyond the JSON example (the example shows the canonical structure, not the exhaustive field list). Adding `approval_contract_json: str = ""` is the correct approach for downstream Phase 6/7 consumption without breaking the typed handoff contract pattern.

**Decision:** Field added. The HandoffContract schema comment explicitly notes "Phase 5+" for this field.

### LLM structured output uses `tool_choice = {"type": "tool"}`

This forces the Anthropic API to return a `tool_use` block, guaranteeing structured JSON. If the API returns a `text` block instead (possible in degenerate cases), the agent treats it as a validation failure and falls back to deterministic prose.

### Historical decision reasons are preserved verbatim

`HistoricalPrecedentEntry.decision_reason` stores the original human decision text exactly as retrieved from the memory store, even if that text contains injection-like language. The text is passed to the LLM inside `<untrusted_input>` tags and is validated as data, not instructions. The risk tier and approval state are never affected by this text.

---

## 8. Phase 6 Readiness

**Phase 6 input requirements:**

| Requirement | Available after Phase 5 |
|-------------|------------------------|
| `contract.risk_tier` set | Yes — `RiskTier` enum value |
| `contract.risk_score` set | Yes — float, 3 decimal places |
| `contract.approval_contract_json` set | Yes — serialized `ApprovalContract` JSON |
| `contract.contract_assembled` = True | Yes |
| `contract.risk_tier_deterministic` = True (implicit) | Yes — schema invariant in `ApprovalContract` |
| `contract.rollback_plan` set | Yes — mechanical derivation or None for PERMANENT |
| `contract.workflow_provenance` includes CONTRACT_AGENT | Yes |
| Four contract sections populated | Yes — all four required sections |
| Pydantic schema validated | Yes — `ApprovalContract.model_validate()` called before serialization |

**Phase 6 readiness: READY**

Phase 6 (Full Orchestration — state transitions, guards, timeouts, retries, selective re-analysis) can consume `approval_contract_json` by deserializing it as `ApprovalContract.model_validate(json.loads(contract.approval_contract_json))`.
