"""
Analyzer Agent tools — thin wrappers over sql_analysis_lib.

PHASE 0 SCAFFOLD — NOT IMPLEMENTED.
Implementation begins in Phase 3 (after sql_analysis_lib is complete in Phase 1).

Every function here is a THIN WRAPPER:
- Calls the corresponding sql_analysis_lib function
- Translates the result into a HandoffContract-compatible structure
- Calls the appropriate MCP server (postgres-reader) for database access
- Does NOT contain analysis logic of its own

Tools (from CLAUDE.md §8):

parse_sql_intent(raw_sql: str) -> ParsedPlan
    Wraps: sql_analysis_lib — SQL parsing
    MCP: none (pure parsing)

validate_schema(table: str, columns: list[str]) -> SchemaValidation
    Wraps: sql_analysis_lib — schema check
    MCP: postgres-reader (SELECT on information_schema)

count_affected_rows(table: str, condition: str | None) -> RowEstimate
    Wraps: sql_analysis_lib.row_estimator
    MCP: postgres-reader (EXPLAIN + pg_stat_user_tables)

trace_foreign_keys(table: str) -> CascadeGraph
    Wraps: sql_analysis_lib.cascade_tracer
    MCP: postgres-reader (information_schema queries)

list_cascade_tables(table: str, condition: str | None) -> list[CascadeEntry]
    Wraps: sql_analysis_lib.cascade_tracer
    MCP: postgres-reader

estimate_api_fanout(table: str, trigger_analysis: TriggerAnalysis, row_count: int) -> list[ExternalTrigger]
    Wraps: sql_analysis_lib.trigger_detector
    MCP: none (calculation from trigger analysis + row count)

classify_operation_type(raw_sql: str) -> OperationType
    Wraps: sql_analysis_lib — SQL classification
    MCP: none (pure parsing)

detect_trigger_side_effects(table: str) -> TriggerAnalysis
    Wraps: sql_analysis_lib.trigger_detector
    MCP: postgres-reader (pg_trigger, pg_proc, pg_extension queries)
"""
