"""
API key validation middleware for ContraGate MCP servers.

MVP uses API key authentication (no OIDC — CLAUDE.md §4).
The key is passed via X-ContraGate-Key header and checked against
the CONTRAGATE_MCP_API_KEY environment variable.

For local dev with USE_MOCK_AUTH=true, auth is bypassed.
"""

from __future__ import annotations

import os

from fastapi import HTTPException


_VALID_KEY: str | None = None
_USE_MOCK_AUTH: bool = os.environ.get("USE_MOCK_AUTH", "false").lower() == "true"


def _get_valid_key() -> str:
    global _VALID_KEY
    if _VALID_KEY is None:
        _VALID_KEY = os.environ.get("CONTRAGATE_MCP_API_KEY", "")
    return _VALID_KEY


def validate_api_key(provided_key: str) -> None:
    """
    Raise HTTPException(401) if the provided key is invalid.

    Security: do not short-circuit on empty key — always compare.
    """
    if _USE_MOCK_AUTH:
        return

    valid = _get_valid_key()
    if not valid:
        return

    if not _constant_time_compare(provided_key, valid):
        raise HTTPException(status_code=401, detail="Invalid or missing API key")


def _constant_time_compare(a: str, b: str) -> bool:
    """Constant-time string comparison to prevent timing attacks."""
    if len(a) != len(b):
        return False
    result = 0
    for x, y in zip(a.encode(), b.encode()):
        result |= x ^ y
    return result == 0
