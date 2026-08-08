"""
Analyzer Agent — orchestration.

PHASE 0 SCAFFOLD — NOT IMPLEMENTED.
Implementation begins in Phase 3.

Responsibilities (CLAUDE.md §8 — Agent 1):
- Convert intercepted tool call into structured consequence map
- Produce typed HandoffContract fields:
    intent_summary, parsed_plan, blast_radius, reversibility
- Call 8 tools (thin wrappers in tools.py over sql_analysis_lib):
    parse_sql_intent, validate_schema, count_affected_rows, trace_foreign_keys,
    list_cascade_tables, estimate_api_fanout, classify_operation_type,
    detect_trigger_side_effects

LLM usage:
- Structured output prompting ONLY — model produces typed JSON, never free text
- All plan descriptions wrapped in <untrusted_input> tags before LLM call
- Risk classification is NEVER left to LLM discretion

CRITICAL (CLAUDE.md §22, invariants 2, 3, 11):
- Do NOT allow LLM to produce reversibility classification
- Do NOT allow LLM to produce rollback SQL
- Do NOT pass raw SQL to LLM without untrusted_input wrapping
"""
