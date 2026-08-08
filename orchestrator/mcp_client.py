"""
Thin async HTTP client for calling ContraGate MCP servers.

Each MCP server exposes POST /call with body:
  { "tool": "<tool_name>", "arguments": { ... } }

The client handles auth, structured error propagation, and logging.
Never used for arbitrary external network calls — only ContraGate-internal MCP servers.
"""

from __future__ import annotations

import logging
import os
from typing import Any

import httpx

logger = logging.getLogger(__name__)

MCP_API_KEY = os.environ.get("CONTRAGATE_MCP_API_KEY", "dev-mcp-key")
USE_MOCK_AUTH = os.environ.get("USE_MOCK_AUTH", "true").lower() == "true"

# Default service URLs — overridable via env vars
POSTGRES_READER_URL = os.environ.get(
    "POSTGRES_READER_URL", "http://contragate-mcp:8010"
)
TRANSACTION_SANDBOX_URL = os.environ.get(
    "TRANSACTION_SANDBOX_URL", "http://contragate-mcp:8011"
)
MEMORY_STORE_URL = os.environ.get(
    "MEMORY_STORE_URL", "http://contragate-mcp:8012"
)
AUDIT_LOGGER_URL = os.environ.get(
    "AUDIT_LOGGER_URL", "http://contragate-mcp:8013"
)
POLICY_STORE_URL = os.environ.get(
    "POLICY_STORE_URL", "http://contragate-mcp:8014"
)
NOTIFIER_URL = os.environ.get(
    "NOTIFIER_URL", "http://contragate-notifier:8020"
)


class MCPCallError(Exception):
    """Raised when an MCP server returns an error response."""

    def __init__(self, server: str, tool: str, detail: str) -> None:
        self.server = server
        self.tool = tool
        super().__init__(f"MCP call {server}/{tool} failed: {detail}")


async def call(
    base_url: str,
    tool: str,
    args: dict[str, Any],
    timeout: float = 10.0,
) -> Any:
    """
    Call an MCP server tool and return the result.
    Raises MCPCallError on HTTP or tool errors.
    """
    headers = {"X-ContraGate-Key": MCP_API_KEY}
    payload = {"tool": tool, "arguments": args}
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                f"{base_url}/call",
                json=payload,
                headers=headers,
            )
        response.raise_for_status()
        data = response.json()
        if "error" in data:
            raise MCPCallError(base_url, tool, data["error"])
        return data.get("result", data)
    except httpx.TimeoutException as exc:
        raise MCPCallError(base_url, tool, f"timeout after {timeout}s") from exc
    except httpx.HTTPStatusError as exc:
        raise MCPCallError(base_url, tool, f"HTTP {exc.response.status_code}") from exc


# Convenience wrappers for each MCP server

async def postgres_reader(tool: str, args: dict) -> Any:
    return await call(POSTGRES_READER_URL, tool, args)


async def transaction_sandbox(tool: str, args: dict, timeout: float = 8.0) -> Any:
    return await call(TRANSACTION_SANDBOX_URL, tool, args, timeout=timeout)


async def memory_store(tool: str, args: dict) -> Any:
    return await call(MEMORY_STORE_URL, tool, args)


async def audit_logger(tool: str, args: dict) -> Any:
    return await call(AUDIT_LOGGER_URL, tool, args)


async def policy_store(tool: str, args: dict) -> Any:
    return await call(POLICY_STORE_URL, tool, args)


async def notifier(tool: str, args: dict) -> Any:
    return await call(NOTIFIER_URL, tool, args)
