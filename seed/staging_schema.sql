-- ContraGate Staging Database Initialization
-- The staging database is the transaction sandbox target.
-- It mirrors the production demo schema and has sandbox-aware triggers.
--
-- For local dev: runs in a separate schema (contragate_staging) within contragate-db.
-- For Railway: runs on a separate PostgreSQL instance.

-- ─── Staging schema ───────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS contragate_staging;

-- ─── Mirror tables from demo_schema.sql ───────────────────────────────────────
CREATE TABLE IF NOT EXISTS contragate_staging.users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    subscription_tier   TEXT NOT NULL DEFAULT 'free',
    subscription_status TEXT NOT NULL DEFAULT 'active'
);

CREATE TABLE IF NOT EXISTS contragate_staging.orders (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES contragate_staging.users(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'active',
    total_value NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stg_orders_user_id ON contragate_staging.orders(user_id);
CREATE INDEX IF NOT EXISTS idx_stg_orders_status ON contragate_staging.orders(status);
CREATE INDEX IF NOT EXISTS idx_stg_orders_created_at ON contragate_staging.orders(created_at);

CREATE TABLE IF NOT EXISTS contragate_staging.invoices (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id    UUID NOT NULL REFERENCES contragate_staging.orders(id) ON DELETE CASCADE,
    amount      NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    status      TEXT NOT NULL DEFAULT 'pending',
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stg_invoices_order_id ON contragate_staging.invoices(order_id);

CREATE TABLE IF NOT EXISTS contragate_staging.notifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES contragate_staging.users(id) ON DELETE CASCADE,
    type        TEXT NOT NULL DEFAULT 'email',
    payload     JSONB NOT NULL DEFAULT '{}',
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_stg_notifications_user_id ON contragate_staging.notifications(user_id);

-- ─── Seed staging with realistic data ─────────────────────────────────────────
-- Must match production row counts to produce realistic cascade row diffs.

INSERT INTO contragate_staging.users (email, created_at, last_active, subscription_tier, subscription_status)
SELECT
    'user' || i || '@example.com',
    NOW() - (random() * INTERVAL '4 years'),
    CASE
        WHEN i <= 14000 THEN NOW() - (random() * INTERVAL '1 year' + INTERVAL '2 years')
        ELSE NOW() - (random() * INTERVAL '1 year')
    END,
    CASE (i % 4)
        WHEN 0 THEN 'free'
        WHEN 1 THEN 'pro'
        WHEN 2 THEN 'enterprise'
        ELSE 'free'
    END,
    CASE
        WHEN i <= 5000 THEN 'cancelled'
        ELSE 'active'
    END
FROM generate_series(1, 50000) AS i
ON CONFLICT (email) DO NOTHING;

INSERT INTO contragate_staging.orders (user_id, status, total_value, created_at)
SELECT
    u.id,
    CASE (ROW_NUMBER() OVER () % 5)
        WHEN 0 THEN 'cancelled'
        WHEN 1 THEN 'completed'
        ELSE 'active'
    END,
    (random() * 500 + 10)::NUMERIC(10,2),
    NOW() - (random() * INTERVAL '3 years')
FROM contragate_staging.users u
CROSS JOIN generate_series(1, 4) AS n
LIMIT 180000;

INSERT INTO contragate_staging.invoices (order_id, amount, status, issued_at)
SELECT
    o.id,
    o.total_value * (0.5 + random() * 0.5),
    CASE (ROW_NUMBER() OVER () % 4)
        WHEN 0 THEN 'paid'
        WHEN 1 THEN 'pending'
        WHEN 2 THEN 'overdue'
        ELSE 'paid'
    END,
    NOW() - (random() * INTERVAL '3 years')
FROM contragate_staging.orders o
CROSS JOIN generate_series(1, 3) AS n
LIMIT 430000;

INSERT INTO contragate_staging.notifications (user_id, type, payload, sent_at)
SELECT
    u.id,
    CASE (ROW_NUMBER() OVER () % 3)
        WHEN 0 THEN 'email'
        WHEN 1 THEN 'sms'
        ELSE 'push'
    END,
    jsonb_build_object('template', 'marketing_' || (ROW_NUMBER() OVER () % 10)),
    NOW() - (random() * INTERVAL '3 years')
FROM contragate_staging.users u
CROSS JOIN generate_series(1, 50) AS n
LIMIT 2100000;

ANALYZE contragate_staging.users;
ANALYZE contragate_staging.orders;
ANALYZE contragate_staging.invoices;
ANALYZE contragate_staging.notifications;
