-- PII-tagged schema: columns named email, phone, ssn, date_of_birth trigger PII detection.
-- cg_customer_profiles has multiple PII columns.
-- cg_txn_log has no PII columns but has high row count for EXPLAIN cost testing.

CREATE TABLE IF NOT EXISTS cg_customer_profiles (
    id              BIGSERIAL PRIMARY KEY,
    email           TEXT NOT NULL,
    phone           TEXT,
    ssn             TEXT,
    date_of_birth   DATE,
    address         TEXT
);

CREATE TABLE IF NOT EXISTS cg_txn_log (
    id          BIGSERIAL PRIMARY KEY,
    amount      NUMERIC(12, 2),
    merchant    TEXT,
    occurred_at TIMESTAMPTZ DEFAULT NOW()
);

-- Non-transactional trigger on customer_profiles using pg_net
-- (pg_net may not be installed in test environment; trigger body is inspected via pg_proc)
CREATE OR REPLACE FUNCTION cg_notify_external_system()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    PERFORM net.http_post(
        url := 'https://hooks.internal/customer-deleted',
        body := json_build_object('id', OLD.id)::text
    );
    RETURN OLD;
END;
$$;
