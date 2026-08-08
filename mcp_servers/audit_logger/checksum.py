"""
SHA-256 checksum helpers for the audit-logger MCP server.

Extracted so that BaseAgent can import hash_payload() without depending
on the full audit_logger server module (which has psycopg2 imports that
fail in unit test environments without PostgreSQL).
"""
from __future__ import annotations

import hashlib
import json
from typing import Any


def compute_checksum(data: dict) -> str:
    """Canonical SHA-256 of a dict — used for tamper-evidence on audit rows."""
    canonical = json.dumps(data, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


def hash_payload(payload: Any) -> str:
    """SHA-256 of any JSON-serializable value — used for tool call I/O hashing."""
    canonical = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()
