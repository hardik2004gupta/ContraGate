"""
Tests for trigger_detector.py — requires live PostgreSQL connection.

Sets up tables and trigger functions from the pii_tagged schema fixture,
then verifies pg_net and dblink detection, no-trigger tables, and the
configurable extension list.

pg_net tests are conditionally skipped when pg_net is not installed in the
test PostgreSQL instance (the trigger function body is still inspected via
pg_proc, but classification requires the extension to be in pg_extension).

Tests are skipped when no database is available.
"""

from __future__ import annotations

import pytest

from sql_analysis_lib.trigger_detector import TriggerAnalysis, TriggerInfo, detect_triggers
from .conftest import requires_db


def _pg_net_installed(conn) -> bool:
    """Return True if pg_net extension is present in pg_extension catalog."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'pg_net'")
        return cur.fetchone() is not None


def _dblink_installed(conn) -> bool:
    """Return True if dblink extension is present in pg_extension catalog."""
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_extension WHERE extname = 'dblink'")
        return cur.fetchone() is not None


@pytest.fixture(scope="module")
def schema(pg_conn, pii_tagged_schema, ecommerce_schema, simple_fk_schema):
    """Apply schemas and create test triggers for detection testing."""
    requires_db(pg_conn)

    with pg_conn.cursor() as cur:
        # pg_net-style trigger on cg_customer_profiles (function already created by pii_tagged.sql)
        # Create the trigger binding
        cur.execute("""
            DROP TRIGGER IF EXISTS cg_after_delete_notify ON cg_customer_profiles
        """)
        cur.execute("""
            CREATE TRIGGER cg_after_delete_notify
            AFTER DELETE ON cg_customer_profiles
            FOR EACH ROW EXECUTE FUNCTION cg_notify_external_system()
        """)

        # dblink-style trigger on cg_orders
        cur.execute("""
            CREATE OR REPLACE FUNCTION cg_sync_remote_on_delete()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                PERFORM dblink_exec(
                    'host=analytics.internal dbname=warehouse',
                    'INSERT INTO order_deletes VALUES (' || OLD.id || ')'
                );
                RETURN OLD;
            END;
            $$
        """)
        cur.execute("""
            DROP TRIGGER IF EXISTS cg_after_delete_sync ON cg_orders
        """)
        cur.execute("""
            CREATE TRIGGER cg_after_delete_sync
            AFTER DELETE ON cg_orders
            FOR EACH ROW EXECUTE FUNCTION cg_sync_remote_on_delete()
        """)

        # Pure transactional trigger on cg_employees (no external call)
        cur.execute("""
            CREATE OR REPLACE FUNCTION cg_log_salary_change()
            RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                INSERT INTO cg_audit_events(user_id, action)
                VALUES (NEW.id, 'salary_changed');
                RETURN NEW;
            END;
            $$
        """)
        cur.execute("""
            DROP TRIGGER IF EXISTS cg_after_update_salary ON cg_employees
        """)
        cur.execute("""
            CREATE TRIGGER cg_after_update_salary
            AFTER UPDATE OF salary ON cg_employees
            FOR EACH ROW EXECUTE FUNCTION cg_log_salary_change()
        """)

    pg_conn.commit()
    return pg_conn


class TestDetectTriggersNoTriggers:
    def test_table_with_no_triggers(self, schema):
        result = detect_triggers("cg_departments", schema)
        assert isinstance(result, TriggerAnalysis)
        assert result.table == "cg_departments"
        assert result.triggers == []
        assert result.has_permanent_side_effects is False
        assert result.non_transactional_triggers == []

    def test_no_triggers_analysis_structure(self, schema):
        result = detect_triggers("cg_invoices", schema)
        assert result.has_permanent_side_effects is False


class TestDetectTriggersPgNet:
    def test_pg_net_trigger_name_always_present(self, schema):
        """Trigger is visible in pg_trigger regardless of pg_net installation status."""
        result = detect_triggers("cg_customer_profiles", schema)
        names = [t.trigger_name for t in result.triggers]
        assert "cg_after_delete_notify" in names

    def test_pg_net_classified_only_when_installed(self, schema):
        """
        Non-transactional classification requires pg_net in pg_extension.

        If pg_net is installed: has_permanent_side_effects=True, classified as pg_net.
        If pg_net is NOT installed: trigger body still contains net.http_post(), but
        classification must NOT fire — the extension is not actually installed.
        """
        if _pg_net_installed(schema):
            result = detect_triggers("cg_customer_profiles", schema)
            assert result.has_permanent_side_effects is True
            exts = [t.non_transactional_extension for t in result.non_transactional_triggers]
            assert "pg_net" in exts
        else:
            pytest.skip("pg_net not installed in test database — classification skipped per pg_extension check")

    def test_pg_net_trigger_in_non_transactional_list(self, schema):
        if not _pg_net_installed(schema):
            pytest.skip("pg_net not installed")
        result = detect_triggers("cg_customer_profiles", schema)
        assert len(result.non_transactional_triggers) >= 1

    def test_pg_net_extension_identified(self, schema):
        if not _pg_net_installed(schema):
            pytest.skip("pg_net not installed")
        result = detect_triggers("cg_customer_profiles", schema)
        exts = [t.non_transactional_extension for t in result.non_transactional_triggers]
        assert "pg_net" in exts

    def test_pg_net_trigger_event_is_delete(self, schema):
        if not _pg_net_installed(schema):
            pytest.skip("pg_net not installed")
        result = detect_triggers("cg_customer_profiles", schema)
        nt = result.non_transactional_triggers[0]
        assert "DELETE" in nt.event

    def test_endpoint_extracted_from_function_body(self, schema):
        if not _pg_net_installed(schema):
            pytest.skip("pg_net not installed")
        result = detect_triggers("cg_customer_profiles", schema)
        nt = result.non_transactional_triggers[0]
        assert nt.target_endpoint is not None
        assert "hooks.internal" in nt.target_endpoint

    def test_uninstalled_extension_pattern_match_not_classified(self, schema):
        """
        Even if a trigger function body contains 'net.http_post(...)', if pg_net is
        absent from pg_extension, detect_triggers must not classify it as non-transactional.
        This verifies the pg_extension catalog cross-reference (High finding #5).
        """
        if _pg_net_installed(schema):
            pytest.skip("pg_net IS installed — this test only applies when pg_net is absent")
        result = detect_triggers("cg_customer_profiles", schema)
        assert result.has_permanent_side_effects is False
        assert result.non_transactional_triggers == []


class TestDetectTriggersDblink:
    def test_dblink_trigger_detected(self, schema):
        if not _dblink_installed(schema):
            pytest.skip("dblink not installed")
        result = detect_triggers("cg_orders", schema)
        assert result.has_permanent_side_effects is True

    def test_dblink_extension_identified(self, schema):
        if not _dblink_installed(schema):
            pytest.skip("dblink not installed")
        result = detect_triggers("cg_orders", schema)
        exts = [t.non_transactional_extension for t in result.non_transactional_triggers]
        assert "dblink" in exts

    def test_dblink_trigger_name_always_present(self, schema):
        """Trigger is visible in pg_trigger regardless of dblink installation status."""
        result = detect_triggers("cg_orders", schema)
        names = [t.trigger_name for t in result.triggers]
        assert "cg_after_delete_sync" in names


class TestDetectTriggersTransactional:
    def test_transactional_trigger_not_in_nt_list(self, schema):
        """cg_log_salary_change uses only INSERT — no external extension."""
        result = detect_triggers("cg_employees", schema)
        nt_names = [t.trigger_name for t in result.non_transactional_triggers]
        assert "cg_after_update_salary" not in nt_names

    def test_transactional_trigger_invokes_non_transactional_false(self, schema):
        result = detect_triggers("cg_employees", schema)
        salary_trigger = next(
            (t for t in result.triggers if t.trigger_name == "cg_after_update_salary"),
            None,
        )
        if salary_trigger:
            assert salary_trigger.invokes_non_transactional is False


class TestDetectTriggersConfigurableExtensions:
    def test_custom_extension_list_detects_dblink(self, schema):
        if not _dblink_installed(schema):
            pytest.skip("dblink not installed — pg_extension cross-reference requires the extension to be present")
        result = detect_triggers("cg_orders", schema, non_transactional_extensions=["dblink"])
        assert result.has_permanent_side_effects is True

    def test_empty_extension_list_detects_nothing(self, schema):
        result = detect_triggers("cg_orders", schema, non_transactional_extensions=[])
        assert result.has_permanent_side_effects is False

    def test_trigger_info_structure(self, schema):
        result = detect_triggers("cg_customer_profiles", schema)
        assert len(result.triggers) >= 1
        t = result.triggers[0]
        assert isinstance(t, TriggerInfo)
        assert t.trigger_name
        assert t.table_name == "cg_customer_profiles"
        assert t.event in ("INSERT", "DELETE", "UPDATE", "INSERT OR DELETE",
                           "INSERT OR UPDATE", "DELETE OR UPDATE",
                           "INSERT OR DELETE OR UPDATE")
        assert t.timing in ("BEFORE", "AFTER", "INSTEAD OF")
