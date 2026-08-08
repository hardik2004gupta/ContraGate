"""
Database connection management for sql_analysis_lib.

Two connection functions:
  - get_production_conn: read-only, production database
  - get_staging_conn: read-write, staging database (BEGIN/ROLLBACK sandbox only)

Security invariants enforced here:
  - get_staging_conn fails closed if STAGING_DATABASE_URL is missing or equals
    the production URL — there is NO fallback to DATABASE_URL.
  - get_production_conn enforces SESSION read-only as defense in depth
    (primary enforcement is the MCP postgres-reader SELECT-only role).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

import psycopg2
import psycopg2.extras


@dataclass
class ConnectionConfig:
    production_url: str
    staging_url: str
    statement_timeout_ms: int = 5000

    @classmethod
    def from_env(cls) -> "ConnectionConfig":
        """
        Build config from DATABASE_URL and STAGING_DATABASE_URL env vars.

        Raises EnvironmentError if STAGING_DATABASE_URL is absent or equals
        DATABASE_URL — no fallback is permitted (security invariant #4, §22).
        """
        prod = os.environ["DATABASE_URL"]
        staging = os.environ.get("STAGING_DATABASE_URL")

        if staging is None:
            raise EnvironmentError(
                "STAGING_DATABASE_URL must be set explicitly. "
                "Falling back to DATABASE_URL is prohibited — "
                "the staging database must be a separate, writable instance, "
                "never the production database. "
                "(ContraGate security invariant #4: no production writes from simulation)"
            )
        if staging == prod:
            raise EnvironmentError(
                "STAGING_DATABASE_URL must not equal DATABASE_URL. "
                "The staging sandbox instance must be physically separate from "
                "production to prevent simulation writes from touching production data. "
                "(ContraGate security invariant #4)"
            )

        timeout = int(os.environ.get("STATEMENT_TIMEOUT_MS", "5000"))
        return cls(production_url=prod, staging_url=staging, statement_timeout_ms=timeout)


def _apply_timeout(conn: psycopg2.extensions.connection, timeout_ms: int) -> None:
    with conn.cursor() as cur:
        cur.execute("SET statement_timeout = %s", (timeout_ms,))
    conn.commit()


def get_production_conn(config: ConnectionConfig) -> psycopg2.extensions.connection:
    """
    Return a psycopg2 connection to the production database.

    Enforces:
      - statement_timeout immediately on connect
      - SESSION read-only (defense in depth; primary enforcement is the
        postgres-reader MCP server's SELECT-only database role)

    Callers are responsible for closing the connection.
    """
    conn = psycopg2.connect(config.production_url, cursor_factory=psycopg2.extras.RealDictCursor)
    _apply_timeout(conn, config.statement_timeout_ms)
    with conn.cursor() as cur:
        cur.execute("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
    conn.commit()
    return conn


def get_staging_conn(config: ConnectionConfig) -> psycopg2.extensions.connection:
    """
    Return a psycopg2 connection to the staging database.

    Used exclusively by the transaction sandbox (BEGIN/execute/ROLLBACK).
    The staging instance must be network-isolated from external services.

    Pre-condition: config.staging_url != config.production_url (enforced by
    ConnectionConfig.from_env(); callers constructing ConnectionConfig manually
    are responsible for the same guarantee).
    """
    conn = psycopg2.connect(config.staging_url, cursor_factory=psycopg2.extras.RealDictCursor)
    _apply_timeout(conn, config.statement_timeout_ms)
    return conn
