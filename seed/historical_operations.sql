-- ContraGate Historical Operations Seed
-- 20 seeded operations designed to produce specific retrieval behavior in demo scenarios.
-- These are NOT random — each one surfaces at a precise moment in the demo.
--
-- Stored in the operation_memory table with pgvector embeddings.
-- Embeddings are 1536-dimensional zeros here; the memory-store server updates
-- them to real embeddings on first retrieval if they are all-zero.

-- ─── Operation memory table ───────────────────────────────────────────────────
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS contragate_app.operation_memory (
    id                      SERIAL PRIMARY KEY,
    operation_id            TEXT NOT NULL UNIQUE,
    tenant_id               TEXT NOT NULL DEFAULT 'demo_tenant',
    intent_summary          TEXT NOT NULL,
    intent_embedding        VECTOR(1536),                    -- pgvector column
    affected_tables         TEXT[] NOT NULL DEFAULT '{}',
    operation_type          TEXT NOT NULL,
    estimated_rows          INT NOT NULL DEFAULT 0,
    actual_rows             INT,
    outcome                 TEXT NOT NULL CHECK (outcome IN (
                                'APPROVED', 'REJECTED', 'ROLLED_BACK', 'MODIFIED', 'AUTO_REJECTED'
                            )),
    decision_reason         TEXT NOT NULL DEFAULT '',
    blast_radius_delta      FLOAT,                           -- (actual - estimated) / estimated
    reanalysis_count        INT NOT NULL DEFAULT 0,
    submitted_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    decided_at              TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_mem_tenant ON contragate_app.operation_memory(tenant_id);
CREATE INDEX IF NOT EXISTS idx_mem_outcome ON contragate_app.operation_memory(outcome);
CREATE INDEX IF NOT EXISTS idx_mem_tables ON contragate_app.operation_memory USING GIN(affected_tables);
-- pgvector cosine similarity index
CREATE INDEX IF NOT EXISTS idx_mem_embedding ON contragate_app.operation_memory
    USING ivfflat (intent_embedding vector_cosine_ops) WITH (lists = 50);

-- ─── Auto-reject pattern log ──────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contragate_app.auto_reject_patterns (
    id              SERIAL PRIMARY KEY,
    operation_id    TEXT NOT NULL,
    sql_fingerprint TEXT NOT NULL,
    affected_tables TEXT[] NOT NULL DEFAULT '{}',
    operation_type  TEXT NOT NULL,
    rejection_reason TEXT NOT NULL,
    tenant_id       TEXT NOT NULL DEFAULT 'demo_tenant',
    rejected_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_arp_fingerprint ON contragate_app.auto_reject_patterns(sql_fingerprint, tenant_id);
CREATE INDEX IF NOT EXISTS idx_arp_rejected_at ON contragate_app.auto_reject_patterns(rejected_at);

-- ─── Confidence score table ───────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS contragate_app.confidence_scores (
    id              SERIAL PRIMARY KEY,
    table_name      TEXT NOT NULL,
    operation_type  TEXT NOT NULL,
    confidence      FLOAT NOT NULL DEFAULT 0.8,
    sample_count    INT NOT NULL DEFAULT 0,
    last_updated    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tenant_id       TEXT NOT NULL DEFAULT 'demo_tenant',
    UNIQUE (table_name, operation_type, tenant_id)
);

-- ─── 5 REJECTED operations ────────────────────────────────────────────────────

-- cg_1847: Surfaces as #1 in Demo Scenario 3 (DELETE users inactive > 2 years)
INSERT INTO contragate_app.operation_memory (
    operation_id, intent_summary, affected_tables, operation_type,
    estimated_rows, actual_rows, outcome, decision_reason, blast_radius_delta,
    submitted_at, decided_at
) VALUES (
    'cg_1847',
    'Delete user accounts that have been inactive for more than 2 years to free up storage and reduce PII exposure',
    ARRAY['users', 'orders', 'invoices'],
    'DELETE',
    12000,
    14203,
    'REJECTED',
    'Cascade into invoices caused loss of 8,200 billing records. Archival step required before any delete.',
    0.183,
    NOW() - INTERVAL '8 months',
    NOW() - INTERVAL '8 months' + INTERVAL '12 minutes'
) ON CONFLICT (operation_id) DO NOTHING;

-- cg_2203: Surfaces as #2 in Demo Scenario 3 (SendGrid webhook)
INSERT INTO contragate_app.operation_memory (
    operation_id, intent_summary, affected_tables, operation_type,
    estimated_rows, actual_rows, outcome, decision_reason, blast_radius_delta,
    submitted_at, decided_at
) VALUES (
    'cg_2203',
    'Remove inactive users with cancelled subscription status from the users table',
    ARRAY['users', 'notifications'],
    'DELETE',
    8500,
    11247,
    'ROLLED_BACK',
    'SendGrid webhook fired 11,000 calls before operator noticed. 3 hours to suppress campaign. Rolled back after partial execution.',
    0.323,
    NOW() - INTERVAL '10 months',
    NOW() - INTERVAL '10 months' + INTERVAL '25 minutes'
) ON CONFLICT (operation_id) DO NOTHING;

-- cg_3891: Demonstrates policy gate auto-rejection (TRUNCATE orders)
INSERT INTO contragate_app.operation_memory (
    operation_id, intent_summary, affected_tables, operation_type,
    estimated_rows, actual_rows, outcome, decision_reason, blast_radius_delta,
    submitted_at, decided_at
) VALUES (
    'cg_3891',
    'Truncate the orders table to clear all historical order data',
    ARRAY['orders'],
    'DDL',
    180000,
    NULL,
    'AUTO_REJECTED',
    'No backup confirmed. Policy violation — auto-rejected by ContraGate before human review. Rule: POLICY_DDL_NO_BACKUP.',
    NULL,
    NOW() - INTERVAL '5 months',
    NOW() - INTERVAL '5 months'
) ON CONFLICT (operation_id) DO NOTHING;

-- cg_4102: Demonstrates prompt injection risk tag
INSERT INTO contragate_app.operation_memory (
    operation_id, intent_summary, affected_tables, operation_type,
    estimated_rows, actual_rows, outcome, decision_reason, blast_radius_delta,
    submitted_at, decided_at
) VALUES (
    'cg_4102',
    'Update user email addresses from external user-provided input source',
    ARRAY['users'],
    'UPDATE',
    1,
    NULL,
    'REJECTED',
    'Prompt injection risk tag present. External input source — rejected pending security review.',
    NULL,
    NOW() - INTERVAL '3 months',
    NOW() - INTERVAL '3 months' + INTERVAL '5 minutes'
) ON CONFLICT (operation_id) DO NOTHING;

-- cg_5517: Surfaces in Demo Scenario 4 (DELETE orders > 3 years)
INSERT INTO contragate_app.operation_memory (
    operation_id, intent_summary, affected_tables, operation_type,
    estimated_rows, actual_rows, outcome, decision_reason, blast_radius_delta,
    submitted_at, decided_at
) VALUES (
    'cg_5517',
    'Delete cancelled orders older than 3 years from the orders table',
    ARRAY['orders', 'invoices'],
    'DELETE',
    6800,
    7241,
    'REJECTED',
    'Cascade into invoices not acceptable. Need archival to cold storage first.',
    0.062,
    NOW() - INTERVAL '6 months',
    NOW() - INTERVAL '6 months' + INTERVAL '18 minutes'
) ON CONFLICT (operation_id) DO NOTHING;

-- Add rejected ops to auto-reject pattern log
INSERT INTO contragate_app.auto_reject_patterns (
    operation_id, sql_fingerprint, affected_tables, operation_type, rejection_reason
) VALUES
    ('cg_1847', 'DELETE_users_last_active_interval', ARRAY['users','orders','invoices'], 'DELETE',
     'Cascade into invoices caused loss of 8,200 billing records. Archival step required before any delete.'),
    ('cg_3891', 'DDL_TRUNCATE_orders', ARRAY['orders'], 'DDL',
     'No backup confirmed. Policy violation — POLICY_DDL_NO_BACKUP triggered.')
ON CONFLICT DO NOTHING;

-- ─── 5 ROLLED_BACK operations (blast radius > 30% delta) ─────────────────────

INSERT INTO contragate_app.operation_memory (
    operation_id, intent_summary, affected_tables, operation_type,
    estimated_rows, actual_rows, outcome, decision_reason, blast_radius_delta,
    submitted_at, decided_at
) VALUES
(
    'cg_rb_001',
    'Update subscription tier for free users who have not logged in for 6 months',
    ARRAY['users'],
    'UPDATE',
    3000, 4892, 'ROLLED_BACK',
    'Actual row count exceeded estimate by 63%. Rolled back after discovering stale statistics.',
    0.631,
    NOW() - INTERVAL '14 months', NOW() - INTERVAL '14 months' + INTERVAL '8 minutes'
),
(
    'cg_rb_002',
    'Delete notifications older than 18 months',
    ARRAY['notifications'],
    'DELETE',
    100000, 158000, 'ROLLED_BACK',
    'Lock contention caused timeout at 60% completion. Rolled back.',
    0.58,
    NOW() - INTERVAL '12 months', NOW() - INTERVAL '12 months' + INTERVAL '30 minutes'
),
(
    'cg_rb_003',
    'Update order status to archived for orders older than 2 years',
    ARRAY['orders'],
    'UPDATE',
    15000, 22400, 'ROLLED_BACK',
    'Cascade trigger fired unexpectedly updating invoices. Rolled back for safety.',
    0.493,
    NOW() - INTERVAL '9 months', NOW() - INTERVAL '9 months' + INTERVAL '12 minutes'
),
(
    'cg_rb_004',
    'Delete test accounts created before 2022',
    ARRAY['users', 'orders'],
    'DELETE',
    500, 1847, 'ROLLED_BACK',
    'Test accounts had linked production orders. Rolled back to prevent cascade data loss.',
    2.694,
    NOW() - INTERVAL '7 months', NOW() - INTERVAL '7 months' + INTERVAL '5 minutes'
),
(
    'cg_rb_005',
    'Update invoice status to void for invoices linked to cancelled orders',
    ARRAY['invoices', 'orders'],
    'UPDATE',
    8000, 11350, 'ROLLED_BACK',
    'Row count exceeded estimate by 42%. Stale statistics on invoices table. Rolled back.',
    0.419,
    NOW() - INTERVAL '4 months', NOW() - INTERVAL '4 months' + INTERVAL '15 minutes'
)
ON CONFLICT (operation_id) DO NOTHING;

-- Update confidence scores downward for tables with high delta rolled-back ops
INSERT INTO contragate_app.confidence_scores (table_name, operation_type, confidence, sample_count)
VALUES
    ('users',         'UPDATE', 0.65, 3),
    ('users',         'DELETE', 0.60, 4),
    ('orders',        'DELETE', 0.68, 2),
    ('orders',        'UPDATE', 0.72, 2),
    ('notifications', 'DELETE', 0.70, 1),
    ('invoices',      'UPDATE', 0.73, 2)
ON CONFLICT (table_name, operation_type, tenant_id) DO UPDATE
    SET confidence = EXCLUDED.confidence, sample_count = EXCLUDED.sample_count;

-- ─── 5 APPROVED and successful ────────────────────────────────────────────────

INSERT INTO contragate_app.operation_memory (
    operation_id, intent_summary, affected_tables, operation_type,
    estimated_rows, actual_rows, outcome, decision_reason, blast_radius_delta,
    submitted_at, decided_at
) VALUES
(
    'cg_ap_001',
    'Delete test accounts created before 2023 with no associated orders',
    ARRAY['users'],
    'DELETE',
    876, 871, 'APPROVED',
    'Test accounts only — no production data affected. Verified no cascade targets.',
    -0.006,
    NOW() - INTERVAL '11 months', NOW() - INTERVAL '11 months' + INTERVAL '3 minutes'
),
(
    'cg_ap_002',
    'Update subscription tier from legacy to free for 200 inactive accounts',
    ARRAY['users'],
    'UPDATE',
    200, 198, 'APPROVED',
    'Small targeted update with no cascades. PITR snapshot confirmed.',
    -0.01,
    NOW() - INTERVAL '8 months', NOW() - INTERVAL '8 months' + INTERVAL '4 minutes'
),
(
    'cg_ap_003',
    'Insert new notification records for campaign batch',
    ARRAY['notifications'],
    'INSERT',
    500, 500, 'APPROVED',
    'Insert only, no cascades, reversible. Batch size within acceptable range.',
    0.0,
    NOW() - INTERVAL '6 months', NOW() - INTERVAL '6 months' + INTERVAL '2 minutes'
),
(
    'cg_ap_004',
    'Update order status to completed for orders in processing state older than 30 days',
    ARRAY['orders'],
    'UPDATE',
    120, 118, 'APPROVED',
    'Small targeted update, fully reversible via PITR. No external triggers involved.',
    -0.017,
    NOW() - INTERVAL '3 months', NOW() - INTERVAL '3 months' + INTERVAL '6 minutes'
),
(
    'cg_ap_005',
    'Delete expired session tokens from notifications older than 90 days',
    ARRAY['notifications'],
    'DELETE',
    2400, 2387, 'APPROVED',
    'Routine cleanup, no cascades, estimated and actual rows within 1%. Approved.',
    -0.005,
    NOW() - INTERVAL '1 month', NOW() - INTERVAL '1 month' + INTERVAL '5 minutes'
)
ON CONFLICT (operation_id) DO NOTHING;

-- ─── 5 MODIFIED (modify-with-constraints) ────────────────────────────────────

INSERT INTO contragate_app.operation_memory (
    operation_id, intent_summary, affected_tables, operation_type,
    estimated_rows, actual_rows, outcome, decision_reason, blast_radius_delta,
    reanalysis_count, submitted_at, decided_at
) VALUES
(
    'cg_mod_001',
    'Delete orders with status cancelled older than 3 years',
    ARRAY['orders', 'invoices'],
    'DELETE',
    7241, 892, 'MODIFIED',
    'Scope to orders where total_value < 10.00 only. Tier dropped to STANDARD after modification.',
    -0.877,
    2,
    NOW() - INTERVAL '15 months', NOW() - INTERVAL '15 months' + INTERVAL '20 minutes'
),
(
    'cg_mod_002',
    'Update email domain for users from legacy domain',
    ARRAY['users'],
    'UPDATE',
    8500, 3200, 'MODIFIED',
    'Limit to users created before 2021 only. Reduced scope accepted.',
    -0.624,
    1,
    NOW() - INTERVAL '13 months', NOW() - INTERVAL '13 months' + INTERVAL '15 minutes'
),
(
    'cg_mod_003',
    'Delete notifications of type sms older than 12 months',
    ARRAY['notifications'],
    'DELETE',
    45000, 12000, 'MODIFIED',
    'Scope to notifications older than 24 months only. Tier dropped from FULL to STANDARD.',
    -0.733,
    1,
    NOW() - INTERVAL '10 months', NOW() - INTERVAL '10 months' + INTERVAL '18 minutes'
),
(
    'cg_mod_004',
    'Update invoice status to void for all pending invoices',
    ARRAY['invoices'],
    'UPDATE',
    22000, 8500, 'MODIFIED',
    'Limit to invoices older than 6 months with no associated active order. Scope acceptable.',
    -0.614,
    2,
    NOW() - INTERVAL '7 months', NOW() - INTERVAL '7 months' + INTERVAL '25 minutes'
),
(
    'cg_mod_005',
    'Delete all free tier users who have never placed an order',
    ARRAY['users'],
    'DELETE',
    18000, 5200, 'MODIFIED',
    'Added constraint: only users created before 2022 with last_active > 18 months ago. Reduced cascade risk.',
    -0.711,
    1,
    NOW() - INTERVAL '5 months', NOW() - INTERVAL '5 months' + INTERVAL '22 minutes'
)
ON CONFLICT (operation_id) DO NOTHING;
