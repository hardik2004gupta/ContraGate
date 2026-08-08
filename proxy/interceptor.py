"""
Tool call interception and routing.

Parses incoming JSON-RPC tool calls, extracts SQL, builds the initial manifest,
and determines the routing path (fast-path vs. full pipeline).

Security: raw SQL is treated as data, never as an instruction to any LLM.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

_KNOWN_SQL_TOOLS = frozenset({
    "execute_query", "run_query", "query", "execute_sql",
    "run_sql", "insert", "update", "delete", "select",
    "postgres_query", "pg_query",
})

_DML_WRITE_PATTERN = re.compile(
    r"^\s*(INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b",
    re.IGNORECASE,
)


class ToolCallManifest:
    """Parsed representation of an incoming MCP tool call."""

    __slots__ = (
        "tool_name", "sql", "intent", "submitted_by",
        "source_type", "tenant_id", "raw_request",
        "target_mcp_url",
    )

    def __init__(
        self,
        tool_name: str,
        sql: str,
        intent: str | None,
        submitted_by: str,
        source_type: str,
        tenant_id: str,
        raw_request: dict[str, Any],
        target_mcp_url: str,
    ) -> None:
        self.tool_name = tool_name
        self.sql = sql
        self.intent = intent
        self.submitted_by = submitted_by
        self.source_type = source_type
        self.tenant_id = tenant_id
        self.raw_request = raw_request
        self.target_mcp_url = target_mcp_url

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "sql": self.sql,
            "intent": self.intent,
            "submitted_by": self.submitted_by,
            "source_type": self.source_type,
            "tenant_id": self.tenant_id,
            "target_mcp_url": self.target_mcp_url,
        }

    def is_write_operation(self) -> bool:
        return bool(_DML_WRITE_PATTERN.match(self.sql))


class InterceptionError(Exception):
    """Raised when the incoming tool call cannot be parsed."""


def intercept(
    body: dict[str, Any],
    caller_id: str = "unknown",
    source_type: str = "internal_system",
    tenant_id: str = "demo_tenant",
    target_mcp_url: str = "",
) -> ToolCallManifest:
    """
    Parse an incoming JSON-RPC tool call and extract the SQL manifest.

    Supports two formats:
      JSON-RPC:  { "method": "tools/call", "params": { "name": "...", "arguments": {...} } }
      Simple:    { "tool_name": "...", "sql": "..." }

    Raises InterceptionError if required fields are absent.
    """
    # Normalize JSON-RPC format to simple format
    if "method" in body and "params" in body:
        params = body["params"]
        tool_name = params.get("name", "unknown_tool")
        args = params.get("arguments", params.get("input", {}))
    elif "tool" in body:
        tool_name = body["tool"]
        args = body.get("args", body.get("arguments", body))
    else:
        tool_name = body.get("tool_name", "unknown_tool")
        args = body

    # Extract SQL from common argument names
    sql: str | None = (
        args.get("sql")
        or args.get("query")
        or args.get("statement")
        or args.get("command")
    )
    if not sql or not sql.strip():
        raise InterceptionError(
            f"Tool call '{tool_name}' does not contain a recognizable SQL argument"
        )

    intent = args.get("intent") or args.get("description")
    submitted_by = caller_id

    logger.info(
        "Intercepted tool call",
        extra={
            "tool_name": tool_name,
            "source_type": source_type,
            "is_write": bool(_DML_WRITE_PATTERN.match(sql)),
        },
    )

    return ToolCallManifest(
        tool_name=tool_name,
        sql=sql.strip(),
        intent=intent,
        submitted_by=submitted_by,
        source_type=source_type,
        tenant_id=tenant_id,
        raw_request=body,
        target_mcp_url=target_mcp_url,
    )
