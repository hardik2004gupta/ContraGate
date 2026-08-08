# SQL Analysis Lib Test Fixtures

This directory contains the test fixtures for `sql_analysis_lib` covering all 30 SQL operation
scenarios required by CLAUDE.md §12.

## Directory Structure

```
fixtures/
├── schemas/           SQL schema setup files (loaded by conftest.py)
│   ├── ecommerce.sql      Main e-commerce schema (users, orders, invoices, etc.)
│   ├── simple_fk.sql      Departments → employees (single CASCADE level)
│   ├── deep_cascade.sql   6-level chain (cg_l1 → cg_l6) for depth-limit tests
│   └── pii_tagged.sql     PII tables with pg_net trigger function
└── operations/        JSON fixture files (one per scenario group)
    ├── ddl_operations.json      8 DDL scenarios (DROP, TRUNCATE, ALTER, CREATE INDEX, CREATE TABLE, DDL+DML)
    ├── bulk_delete.json         4 DELETE scenarios (selective, bulk, PII, soft-delete)
    ├── selective_update.json    5 UPDATE scenarios (selective, bulk, PII, external payment, CTE)
    ├── cascade_delete.json      3 CASCADE DELETE scenarios (single-level, deep chain 6+, simple FK)
    ├── trigger_operations.json  3 trigger scenarios (pg_net, dblink, UPDATE+pg_net PARTIAL)
    ├── read_operations.json     3 SELECT scenarios (simple, PII, high-cost)
    └── edge_cases.json          4 edge-case scenarios (INSERT single, INSERT batch, JOIN update, nested DELETE)
```

**Total: 8 + 4 + 5 + 3 + 3 + 3 + 4 = 30 scenarios**

## Fixture JSON Format

Each JSON file contains an array of scenario objects:

```json
[
  {
    "scenario_id": "ddl_001",
    "description": "DROP TABLE — always PERMANENT",
    "operation_type": "DROP",
    "table": "cg_audit_events",
    "sql": "DROP TABLE cg_audit_events",
    "condition": null,
    "schema_fixture": "ecommerce",
    "inputs": {
      "has_soft_delete_column": false,
      "pitr_confirmed": false,
      "pitr_window_hours": null,
      "estimated_rows": 0
    },
    "expected": {
      "reversibility": "PERMANENT",
      "reversibility_reason_contains": "DDL"
    }
  }
]
```

## The 30 Scenarios (CLAUDE.md §12)

| # | CLAUDE.md Scenario | Fixture ID | File | Reversibility |
|---|--------------------|------------|------|---------------|
| 1 | Simple SELECT on non-PII table | read_001 | read_operations.json | REVERSIBLE_AUTOMATED |
| 2 | SELECT on PII table | read_002 | read_operations.json | REVERSIBLE_AUTOMATED |
| 3 | SELECT with EXPLAIN cost > 50,000 | read_003 | read_operations.json | REVERSIBLE_AUTOMATED |
| 4 | INSERT single row | edge_001 | edge_cases.json | REVERSIBLE_AUTOMATED |
| 5 | INSERT batch (>1,000 rows) | edge_002 | edge_cases.json | REVERSIBLE_AUTOMATED |
| 6 | UPDATE selective (small row count) | upd_001 | selective_update.json | REVERSIBLE_PITR |
| 7 | UPDATE bulk (>10,000 rows) | upd_002 | selective_update.json | PERMANENT |
| 8 | UPDATE bulk on PII table | upd_003 | selective_update.json | REVERSIBLE_PITR |
| 9 | UPDATE with FK side effects | upd_004 | selective_update.json | REVERSIBLE_PITR |
| 10 | DELETE selective (small row count) | del_001 | bulk_delete.json | PERMANENT |
| 11 | DELETE bulk (>10,000 rows) | del_002 | bulk_delete.json | PERMANENT |
| 12 | DELETE with single-level cascade | cas_001 | cascade_delete.json | REVERSIBLE_AUTOMATED |
| 13 | DELETE with multi-level cascade (3+ levels) | cas_002 | cascade_delete.json | PERMANENT |
| 14 | DELETE with pg_net trigger (external side effect) | trg_001 | trigger_operations.json | PERMANENT |
| 15 | DELETE with dblink trigger | trg_002 | trigger_operations.json | PERMANENT |
| 16 | DELETE without soft-delete column | del_001 | bulk_delete.json | PERMANENT |
| 17 | DELETE with soft-delete column (deleted_at) | del_004 | bulk_delete.json | REVERSIBLE_AUTOMATED |
| 18 | TRUNCATE table | ddl_002 | ddl_operations.json | PERMANENT |
| 19 | DROP TABLE | ddl_001 | ddl_operations.json | PERMANENT |
| 20 | ALTER TABLE ADD COLUMN (non-nullable) | ddl_003 | ddl_operations.json | PERMANENT |
| 21 | ALTER TABLE DROP COLUMN | ddl_004 | ddl_operations.json | PERMANENT |
| 22 | ALTER TABLE RENAME | ddl_005 | ddl_operations.json | PERMANENT |
| 23 | CREATE INDEX | ddl_006 | ddl_operations.json | PERMANENT |
| 24 | CREATE TABLE | ddl_007 | ddl_operations.json | PERMANENT |
| 25 | DELETE on PII table | del_003 | bulk_delete.json | REVERSIBLE_PITR |
| 26 | UPDATE on external payment table | upd_004 | selective_update.json | REVERSIBLE_PITR |
| 27 | Complex multi-table JOIN update | edge_003 | edge_cases.json | PERMANENT |
| 28 | Nested subquery DELETE | edge_004 | edge_cases.json | REVERSIBLE_AUTOMATED |
| 29 | CTE-based UPDATE | upd_005 | selective_update.json | PERMANENT |
| 30 | DDL + DML in transaction | ddl_008 | ddl_operations.json | PERMANENT |

Notes:
- Scenarios 10 and 16 share fixture del_001 (DELETE without soft-delete satisfies both "selective small" and "no soft-delete").
- Scenarios 9 and 26 share fixture upd_004 (UPDATE on payment table with FK side effects).
- Scenario 12 (cas_001): cg_users has a soft-delete column, making the classification REVERSIBLE_AUTOMATED despite the cascade.

All scenarios are tested via the integration test suite in `tests/test_reversibility_rules.py`,
`tests/test_cascade_tracer.py`, and `tests/test_trigger_detector.py`.
