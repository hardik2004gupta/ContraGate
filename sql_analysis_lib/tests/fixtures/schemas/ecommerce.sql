-- E-commerce demo schema: users, orders, invoices, notifications, payment_methods
-- FK chain: orders → users, invoices → orders, notifications → users
-- users.deleted_at is the soft-delete column
-- users.email/phone are PII columns

CREATE TABLE IF NOT EXISTS cg_users (
    id          BIGSERIAL PRIMARY KEY,
    email       TEXT NOT NULL,
    phone       TEXT,
    full_name   TEXT,
    deleted_at  TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS cg_orders (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES cg_users(id) ON DELETE CASCADE,
    status      TEXT NOT NULL DEFAULT 'pending',
    total       NUMERIC(12, 2),
    created_at  TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cg_invoices (
    id          BIGSERIAL PRIMARY KEY,
    order_id    BIGINT REFERENCES cg_orders(id) ON DELETE CASCADE,
    amount      NUMERIC(12, 2),
    issued_at   TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cg_notifications (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT REFERENCES cg_users(id) ON DELETE CASCADE,
    message     TEXT,
    sent_at     TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS cg_payment_methods (
    id              BIGSERIAL PRIMARY KEY,
    user_id         BIGINT REFERENCES cg_users(id) ON DELETE RESTRICT,
    card_last4      TEXT,
    stripe_token    TEXT
);

CREATE TABLE IF NOT EXISTS cg_audit_events (
    id          BIGSERIAL PRIMARY KEY,
    user_id     BIGINT,
    action      TEXT,
    occurred_at TIMESTAMPTZ DEFAULT NOW()
);
