-- ContraGate Sandbox Trigger Infrastructure
-- Creates sandbox_trigger_log table and sandbox-aware trigger functions.
--
-- The sandbox_mode flag (app.sandbox_mode = 'true') is checked by every trigger
-- that would fire external calls. In sandbox mode, the trigger logs the
-- "would-have-fired" call to sandbox_trigger_log instead of executing it.

-- ─── Sandbox trigger log ──────────────────────────────────────────────────────
-- Must exist in both app and staging schemas so triggers can write to it.
CREATE TABLE IF NOT EXISTS contragate_staging.sandbox_trigger_log (
    id              SERIAL PRIMARY KEY,
    session_id      TEXT NOT NULL,          -- Sandbox session from SET LOCAL
    trigger_name    TEXT NOT NULL,
    table_name      TEXT NOT NULL,
    operation       TEXT NOT NULL,          -- INSERT / UPDATE / DELETE
    target_url      TEXT,
    payload_summary TEXT,
    estimated_calls INT NOT NULL DEFAULT 1,
    fired_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sandbox_log_session
    ON contragate_staging.sandbox_trigger_log(session_id);

-- ─── SendGrid webhook trigger function (sandbox-aware) ────────────────────────
-- This trigger would fire net.http_post() in production.
-- In sandbox mode it logs to sandbox_trigger_log instead.
CREATE OR REPLACE FUNCTION contragate_staging.cg_sendgrid_webhook()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
    v_session_id TEXT;
BEGIN
    v_session_id := current_setting('app.sandbox_session_id', true);

    IF current_setting('app.sandbox_mode', true) = 'true' THEN
        -- Log the would-have-fired call — do NOT execute net.http_post()
        INSERT INTO contragate_staging.sandbox_trigger_log (
            session_id, trigger_name, table_name, operation,
            target_url, payload_summary, estimated_calls
        ) VALUES (
            COALESCE(v_session_id, 'unknown'),
            TG_NAME,
            TG_TABLE_NAME,
            TG_OP,
            'https://hooks.sendgrid.com/event/webhook',
            jsonb_build_object(
                'user_id', OLD.id,
                'email', OLD.email,
                'event', 'user_deleted'
            )::TEXT,
            1
        );
        RETURN OLD;
    ELSE
        -- Production path: would use pg_net to fire actual webhook
        -- PERFORM net.http_post(
        --     url := 'https://hooks.sendgrid.com/event/webhook',
        --     body := jsonb_build_object('user_id', OLD.id, 'email', OLD.email)::TEXT
        -- );
        RETURN OLD;
    END IF;
END;
$$;

-- Attach SendGrid trigger to users table in staging schema
DROP TRIGGER IF EXISTS cg_sendgrid_on_delete ON contragate_staging.users;
CREATE TRIGGER cg_sendgrid_on_delete
    AFTER DELETE ON contragate_staging.users
    FOR EACH ROW
    EXECUTE FUNCTION contragate_staging.cg_sendgrid_webhook();
