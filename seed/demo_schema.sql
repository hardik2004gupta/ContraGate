-- ContraGate Demo Schema
-- E-commerce schema for demonstrating blast radius analysis.
-- Target row counts (from ContraGate MVP.pdf — Online Demo Database):
--   users:         50,000 rows
--   orders:       180,000 rows
--   invoices:     430,000 rows
--   notifications: 2,100,000 rows
--
-- These counts produce realistic cascade behavior:
--   DELETE users WHERE last_active < 2 years → ~14,000 primary rows
--   → orders cascade:        ~2,891 rows
--   → invoices cascade:      ~8,441 rows (via orders)
--   → notifications cascade: ~47,203 rows
--   Total: ~72,738 rows across 4 tables

-- ─── Application schema ────────────────────────────────────────────────────────
CREATE SCHEMA IF NOT EXISTS contragate_app;

-- ─── PII Registry ─────────────────────────────────────────────────────────────
-- Tracks which tables contain PII for policy enforcement.
CREATE TABLE IF NOT EXISTS contragate_app.pii_registry (
    id          SERIAL PRIMARY KEY,
    table_name  TEXT NOT NULL UNIQUE,
    tagged_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    tagged_by   TEXT NOT NULL DEFAULT 'seed'
);

-- ─── Users ────────────────────────────────────────────────────────────────────
-- PII table — triggers POLICY_PII_STANDARD_REVIEW on all operations.
-- No soft-delete column (deleted_at) — DELETE is PERMANENT.
CREATE TABLE IF NOT EXISTS contragate_app.users (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email               TEXT NOT NULL UNIQUE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_active         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    subscription_tier   TEXT NOT NULL DEFAULT 'free',
    subscription_status TEXT NOT NULL DEFAULT 'active'
);

-- ─── Orders ───────────────────────────────────────────────────────────────────
-- PII table — cascades from users.
CREATE TABLE IF NOT EXISTS contragate_app.orders (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES contragate_app.users(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'active',
    total_value NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orders_user_id ON contragate_app.orders(user_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON contragate_app.orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_created_at ON contragate_app.orders(created_at);

-- ─── Invoices ─────────────────────────────────────────────────────────────────
-- PII table — cascades from orders.
CREATE TABLE IF NOT EXISTS contragate_app.invoices (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id    UUID NOT NULL REFERENCES contragate_app.orders(id) ON DELETE CASCADE,
    amount      NUMERIC(10,2) NOT NULL DEFAULT 0.00,
    status      TEXT NOT NULL DEFAULT 'pending',
    issued_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_invoices_order_id ON contragate_app.invoices(order_id);

-- ─── Notifications ────────────────────────────────────────────────────────────
-- Cascades from users.
CREATE TABLE IF NOT EXISTS contragate_app.notifications (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES contragate_app.users(id) ON DELETE CASCADE,
    type        TEXT NOT NULL DEFAULT 'email',
    payload     JSONB NOT NULL DEFAULT '{}',
    sent_at     TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notifications_user_id ON contragate_app.notifications(user_id);

-- ─── PII Tags ─────────────────────────────────────────────────────────────────
INSERT INTO contragate_app.pii_registry (table_name) VALUES
    ('users'), ('orders'), ('invoices')
ON CONFLICT (table_name) DO NOTHING;

-- ─── Seed Data ────────────────────────────────────────────────────────────────
-- 50,000 users — ~28% inactive for > 2 years (14,000 affected by demo DELETE)
INSERT INTO contragate_app.users (email, created_at, last_active, subscription_tier, subscription_status)
SELECT
    'user' || i || '@example.com',
    NOW() - (random() * INTERVAL '4 years'),
    CASE
        WHEN i <= 14000 THEN NOW() - (random() * INTERVAL '1 year' + INTERVAL '2 years')  -- inactive >2 years
        ELSE NOW() - (random() * INTERVAL '1 year')  -- active within 1 year
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

-- 180,000 orders (~3.6 per user on average)
-- Orders tied to inactive users produce the cascade numbers in the demo
INSERT INTO contragate_app.orders (user_id, status, total_value, created_at)
SELECT
    u.id,
    CASE (ROW_NUMBER() OVER () % 5)
        WHEN 0 THEN 'cancelled'
        WHEN 1 THEN 'completed'
        ELSE 'active'
    END,
    (random() * 500 + 10)::NUMERIC(10,2),
    NOW() - (random() * INTERVAL '3 years')
FROM contragate_app.users u
CROSS JOIN generate_series(1, 4) AS n
LIMIT 180000;

-- 430,000 invoices (~2.4 per order on average)
INSERT INTO contragate_app.invoices (order_id, amount, status, issued_at)
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
FROM contragate_app.orders o
CROSS JOIN generate_series(1, 3) AS n
LIMIT 430000;

-- 2,100,000 notifications (~42 per user on average)
INSERT INTO contragate_app.notifications (user_id, type, payload, sent_at)
SELECT
    u.id,
    CASE (ROW_NUMBER() OVER () % 3)
        WHEN 0 THEN 'email'
        WHEN 1 THEN 'sms'
        ELSE 'push'
    END,
    jsonb_build_object('template', 'marketing_' || (ROW_NUMBER() OVER () % 10)),
    NOW() - (random() * INTERVAL '3 years')
FROM contragate_app.users u
CROSS JOIN generate_series(1, 50) AS n
LIMIT 2100000;

-- ─── Update statistics ────────────────────────────────────────────────────────
ANALYZE contragate_app.users;
ANALYZE contragate_app.orders;
ANALYZE contragate_app.invoices;
ANALYZE contragate_app.notifications;
