"""
BaseAgent — shared infrastructure for all ContraGate agents.

Provides:
  call_tool()       — MCP tool invocation with full audit lifecycle
  log_agent_output() — record final agent state output to audit log
  wrap_untrusted()  — tag raw external/user input before LLM processing

Audit lifecycle per call_tool():
  1. Serialize and SHA-256 hash the input arguments
  2. Make the MCP call via the appropriate server wrapper
  3. SHA-256 hash the output
  4. Fire-and-forget append to audit_logger (pipeline never blocks on audit)

Security invariants (CLAUDE.md §22):
  - Invariant 11: Raw SQL is ALWAYS wrapped via wrap_untrusted() before LLM
  - Invariant 14: Every important action must have provenance
  - Audit log failure is non-fatal — pipeline continues
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any

from mcp_servers.audit_logger.checksum import hash_payload

logger = logging.getLogger(__name__)


def wrap_untrusted(text: str) -> str:
    """Wrap raw user/external/agent input before any LLM processing."""
    return f"<untrusted_input>\n{text}\n</untrusted_input>"


class BaseAgent:
    """
    Base class for all three ContraGate agents.

    Subclasses must set AGENT_NAME and implement their primary async method.
    All external access goes through call_tool() — never direct DB connections.
    """

    AGENT_NAME: str = "BaseAgent"

    def __init__(self, operation_id: str, tenant_id: str) -> None:
        self.operation_id = operation_id
        self.tenant_id = tenant_id

    async def call_tool(
        self,
        server: str,
        tool: str,
        args: dict[str, Any],
        timeout: float = 10.0,
    ) -> Any:
        """
        Call an MCP server tool with full audit lifecycle.

        server: 'postgres-reader' | 'audit-logger' | 'policy-store' |
                'memory-store' | 'transaction-sandbox' | 'notifier'
        """
        from orchestrator import mcp_client as mc

        _DISPATCH = {
            "postgres-reader": mc.postgres_reader,
            "audit-logger": mc.audit_logger,
            "policy-store": mc.policy_store,
            "memory-store": mc.memory_store,
            "transaction-sandbox": mc.transaction_sandbox,
            "notifier": mc.notifier,
        }

        fn = _DISPATCH.get(server)
        if fn is None:
            raise ValueError(f"Unknown MCP server: {server!r}")

        ts = datetime.utcnow().isoformat()
        input_hash = hash_payload(args)

        result = await fn(tool, args)

        output_hash = hash_payload(result)

        # Non-blocking audit log — pipeline never waits for this
        try:
            loop = asyncio.get_event_loop()
            loop.create_task(self._log_tool_call(tool, input_hash, output_hash, ts))
        except RuntimeError:
            pass  # No running event loop in tests — skip

        return result

    async def _log_tool_call(
        self,
        tool_name: str,
        input_hash: str,
        output_hash: str,
        timestamp: str,
    ) -> None:
        from orchestrator import mcp_client as mc
        try:
            await mc.audit_logger("log_tool_call", {
                "operation_id": self.operation_id,
                "agent_name": self.AGENT_NAME,
                "tool_name": tool_name,
                "input_hash": input_hash,
                "output_hash": output_hash,
                "timestamp": timestamp,
            })
        except Exception as exc:
            logger.warning(
                "Audit log failed for tool_call (non-fatal): %s",
                exc,
                extra={"operation_id": self.operation_id, "tool": tool_name},
            )

    async def log_agent_output(
        self,
        output_summary: str,
        contract_hash: str,
    ) -> None:
        """Log the agent's final output for this pipeline state."""
        from orchestrator import mcp_client as mc
        try:
            await mc.audit_logger("log_agent_output", {
                "operation_id": self.operation_id,
                "agent_name": self.AGENT_NAME,
                "output_summary": output_summary,
                "handoff_contract_hash": contract_hash,
                "timestamp": datetime.utcnow().isoformat(),
            })
        except Exception as exc:
            logger.warning(
                "Audit log failed for agent_output (non-fatal): %s",
                exc,
                extra={"operation_id": self.operation_id},
            )
