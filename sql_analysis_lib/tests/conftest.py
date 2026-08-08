"""
pytest configuration for sql_analysis_lib integration tests.

Provides:
  pg_conn   — session-scoped psycopg2 connection to test DB
  apply_schema — helper to load a schema SQL fixture
  fixture_loader — loads JSON from tests/fixtures/operations/*.json

Usage:
  pytest sql_analysis_lib/tests -v --pg-url postgresql://user:pw@host/db

  Or set DATABASE_URL environment variable:
  DATABASE_URL=postgresql://... pytest sql_analysis_lib/tests -v

Integration tests are automatically skipped when no database is reachable.
Pure-function tests (explain_parser, reversibility_rules) always run.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

try:
    import psycopg2
    import psycopg2.extras
    PSYCOPG2_AVAILABLE = True
except ImportError:
    PSYCOPG2_AVAILABLE = False

FIXTURES_DIR = Path(__file__).parent / "fixtures"
SCHEMAS_DIR = FIXTURES_DIR / "schemas"
OPERATIONS_DIR = FIXTURES_DIR / "operations"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--pg-url",
        action="store",
        default=None,
        help="PostgreSQL connection URL for integration tests",
    )


def _resolve_pg_url(config: pytest.Config) -> str | None:
    url = config.getoption("--pg-url", default=None)
    if url:
        return url
    return os.environ.get("DATABASE_URL")


@pytest.fixture(scope="session")
def pg_url(pytestconfig: pytest.Config) -> str | None:
    return _resolve_pg_url(pytestconfig)


@pytest.fixture(scope="session")
def pg_conn(pg_url: str | None):
    """
    Session-scoped live psycopg2 connection.

    Yields None if no DATABASE_URL / --pg-url is provided — integration
    tests must call `pytest.skip` when they receive None.
    """
    if not PSYCOPG2_AVAILABLE or not pg_url:
        yield None
        return

    conn = psycopg2.connect(pg_url, cursor_factory=psycopg2.extras.RealDictCursor)
    conn.autocommit = False
    yield conn
    conn.close()


@pytest.fixture(scope="session", autouse=False)
def ecommerce_schema(pg_conn):
    """Apply the e-commerce schema fixture to the test database."""
    if pg_conn is None:
        return
    _apply_sql_file(pg_conn, SCHEMAS_DIR / "ecommerce.sql")


@pytest.fixture(scope="session", autouse=False)
def simple_fk_schema(pg_conn):
    """Apply the simple FK schema fixture to the test database."""
    if pg_conn is None:
        return
    _apply_sql_file(pg_conn, SCHEMAS_DIR / "simple_fk.sql")


@pytest.fixture(scope="session", autouse=False)
def deep_cascade_schema(pg_conn):
    """Apply the deep cascade schema fixture to the test database."""
    if pg_conn is None:
        return
    _apply_sql_file(pg_conn, SCHEMAS_DIR / "deep_cascade.sql")


@pytest.fixture(scope="session", autouse=False)
def pii_tagged_schema(pg_conn):
    """Apply the PII-tagged schema fixture to the test database."""
    if pg_conn is None:
        return
    _apply_sql_file(pg_conn, SCHEMAS_DIR / "pii_tagged.sql")


def _apply_sql_file(conn, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    with conn.cursor() as cur:
        cur.execute(sql)
    conn.commit()


@pytest.fixture(scope="session")
def fixture_loader():
    """Load a JSON fixture file from tests/fixtures/operations/ by stem name."""
    def load(name: str) -> list[dict]:
        path = OPERATIONS_DIR / f"{name}.json"
        return json.loads(path.read_text(encoding="utf-8"))
    return load


def requires_db(conn) -> None:
    """Call at the top of any integration test to skip if no DB is available."""
    if conn is None:
        pytest.skip("No PostgreSQL connection — set DATABASE_URL or use --pg-url")
