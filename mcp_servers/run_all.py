"""
Starts all six ContraGate MCP servers as independent asyncio tasks within
a single process. Used by the Docker container for local development.

In production, each MCP server runs as a separate Railway service.
"""

import asyncio
import logging
import os
import sys

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format='{"time":"%(asctime)s","level":"%(levelname)s","service":"mcp-all","msg":"%(message)s"}',
)


async def run_server(module: str, port: int) -> None:
    """Launch a single MCP server via uvicorn as a subprocess."""
    import subprocess

    proc = await asyncio.create_subprocess_exec(
        sys.executable, "-m", "uvicorn",
        f"{module}:app",
        "--host", "0.0.0.0",
        "--port", str(port),
        "--no-access-log",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    assert proc.stdout is not None
    async for line in proc.stdout:
        logger.info("[%s:%d] %s", module.split(".")[-2], port, line.decode().strip())


async def main() -> None:
    servers = [
        ("mcp_servers.postgres_reader.server", 8010),
        ("mcp_servers.transaction_sandbox.server", 8011),
        ("mcp_servers.memory_store.server", 8012),
        ("mcp_servers.audit_logger.server", 8013),
        ("mcp_servers.policy_store.server", 8014),
        ("mcp_servers.notifier.server", 8015),
    ]

    tasks = [asyncio.create_task(run_server(mod, port)) for mod, port in servers]
    logger.info("Starting %d MCP servers", len(tasks))

    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        logger.info("Shutting down MCP servers")
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    asyncio.run(main())
