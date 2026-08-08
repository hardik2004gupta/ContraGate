-- ContraGate Default Policy Rules
-- 8 default policies seeded at deployment (CLAUDE.md §15).
-- Applied deterministically by the Policy Gate — no LLM involvement.

-- ─── Policy rules table ───────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contragate_app.policy_rules (
    id              SERIAL PRIMARY KEY,
    rule_id         TEXT NOT NULL UNIQUE,
    rule_name       TEXT NOT NULL,
    description     TEXT NOT NULL,
    -- Structured condition: JSON dict checked by the rule engine
    condition       JSONB NOT NULL,
    -- Action taken when rule triggers
    action          TEXT NOT NULL CHECK (action IN (
                        'AUTO_REJECT',
                        'REQUIRE_FULL_CONTRACT',
                        'REQUIRE_BACKUP',
                        'QUEUE_UNTIL_HOURS',
                        'FLAG_PROMPT_INJECTION'
                    )),
    severity        TEXT NOT NULL CHECK (severity IN ('HARD', 'SOFT')),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    tenant_id       TEXT NOT NULL DEFAULT 'demo_tenant',
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_rules_tenant_active
    ON contragate_app.policy_rules(tenant_id, active);

-- ─── Configurable thresholds ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contragate_app.policy_thresholds (
    id          SERIAL PRIMARY KEY,
    key         TEXT NOT NULL,
    value       NUMERIC NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'demo_tenant',
    UNIQUE (key, tenant_id)
);

INSERT INTO contragate_app.policy_thresholds (key, value) VALUES
    ('explain_cost_threshold',          50000),
    ('explain_cost_pii_threshold',     100000),
    ('bulk_row_threshold',               10000),
    ('jaccard_similarity_threshold',       0.3),
    ('auto_reject_lookback_days',           30),
    ('review_timeout_seconds',            1800),
    ('sandbox_statement_timeout_ms',      5000)
ON CONFLICT (key, tenant_id) DO NOTHING;

-- ─── Non-transactional extensions list ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS contragate_app.non_transactional_extensions (
    id          SERIAL PRIMARY KEY,
    extname     TEXT NOT NULL UNIQUE,
    tenant_id   TEXT NOT NULL DEFAULT 'demo_tenant'
);

INSERT INTO contragate_app.non_transactional_extensions (extname) VALUES
    ('pg_net'), ('dblink')
ON CONFLICT (extname) DO NOTHING;

-- ─── Seed default policy rules ────────────────────────────────────────────────

INSERT INTO contragate_app.policy_rules (rule_id, rule_name, description, condition, action, severity) VALUES

-- Rule 1: AUTO-REJECT DROP/TRUNCATE without confirmed backup
('POLICY_DDL_NO_BACKUP',
 'DDL Without Backup',
 'DROP or TRUNCATE without confirmed backup within 24 hours is auto-rejected',
 '{"operation_types": ["DROP", "TRUNCATE"], "requires_backup_flag": true}',
 'AUTO_REJECT',
 'HARD'),

-- Rule 2: PII tables require standard review minimum
('POLICY_PII_STANDARD_REVIEW',
 'PII Table Standard Review',
 'Any operation on PII-tagged tables requires standard review regardless of row count',
 '{"pii_table_involved": true}',
 'REQUIRE_FULL_CONTRACT',
 'SOFT'),

-- Rule 3: External user input gets full contract + prompt injection tag
('POLICY_EXTERNAL_INPUT',
 'External Input Full Review',
 'Operations from external user input sources require full contract review and prompt injection risk tag',
 '{"source_type": "external_user_input"}',
 'REQUIRE_FULL_CONTRACT',
 'SOFT'),

-- Rule 4: Bulk DELETE/UPDATE on sensitive tables
('POLICY_BULK_DELETE_SENSITIVE',
 'Bulk Operation on Sensitive Tables',
 'DELETE or UPDATE affecting >10,000 rows on users, orders, or invoices requires full contract review',
 '{"operation_types": ["DELETE", "UPDATE"], "tables": ["users", "orders", "invoices"], "min_rows": 10000}',
 'REQUIRE_FULL_CONTRACT',
 'SOFT'),

-- Rule 5: Payment webhook operations
('POLICY_PAYMENT_WEBHOOK',
 'Payment Processor Webhook',
 'Operations firing webhooks to external payment processors require full contract review and backup confirmation',
 '{"has_payment_webhook": true}',
 'REQUIRE_FULL_CONTRACT',
 'SOFT'),

-- Rule 6: After-hours queue
('POLICY_AFTER_HOURS',
 'After Hours Submission',
 'Operations submitted outside 07:00-20:00 local time are queued unless on-call approver is designated',
 '{"business_hours_start": 7, "business_hours_end": 20}',
 'QUEUE_UNTIL_HOURS',
 'SOFT'),

-- Rule 7: Previously auto-rejected pattern
('POLICY_AUTO_REJECT_PATTERN',
 'Previously Rejected Pattern',
 'Operations matching a previously auto-rejected pattern within 30 days are auto-rejected with historical reason cited',
 '{"lookback_days": 30}',
 'AUTO_REJECT',
 'HARD'),

-- Rule 8: Expensive read on PII table
('POLICY_PII_EXPENSIVE_READ',
 'Expensive Read on PII Table',
 'Read operations with EXPLAIN cost > 100,000 on PII-tagged tables require standard review',
 '{"operation_types": ["SELECT"], "pii_table_involved": true, "min_explain_cost": 100000}',
 'REQUIRE_FULL_CONTRACT',
 'SOFT')

ON CONFLICT (rule_id) DO NOTHING;
