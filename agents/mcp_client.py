"""
Agent-level MCP client.

Agents import from here rather than directly from orchestrator.mcp_client
so the dependency boundary is explicit and future agent-specific overrides
(e.g., circuit breakers, agent-scoped timeouts) can be added without
touching orchestrator code.

Currently a transparent re-export — all server URLs are shared between
the orchestrator and agent containers.
"""
from orchestrator.mcp_client import (  # noqa: F401
    call,
    MCPCallError,
    postgres_reader,
    transaction_sandbox,
    memory_store,
    audit_logger,
    policy_store,
    notifier,
    POSTGRES_READER_URL,
    TRANSACTION_SANDBOX_URL,
    MEMORY_STORE_URL,
    AUDIT_LOGGER_URL,
    POLICY_STORE_URL,
    NOTIFIER_URL,
)
