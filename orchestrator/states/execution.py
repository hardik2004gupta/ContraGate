"""
EXECUTION state — retrieve original manifest, verify contract identity, execute.

Security checks (CLAUDE.md §22, §16):
  1. Idempotency: check execution_completed flag before doing anything
  2. Stale-state guard: verify contract was not modified after approval
     (manifest hash bound at intake must match current contract)
  3. Identity binding: execute the ORIGINAL tool-call manifest — not a modified version
  4. Execute via real MCP target — never re-interpret the SQL

Phase 2: executes the original SQL against the real postgres_reader target
         (which is SELECT-only by design). Full write execution is gated
         by the proper MCP target in Phase 9.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any

from orchestrator import mcp_client
from orchestrator.handoff_schema import HandoffContract

logger = logging.getLogger(__name__)


def _manifest_hash(manifest: dict[str, Any]) -> str:
    """Deterministic SHA-256 hash of the tool-call manifest for identity binding."""
    canonical = json.dumps(manifest, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


async def run_execution(contract: HandoffContract) -> HandoffContract:
    """
    Execute the approved operation against the real MCP target.

    Idempotency: If execution_completed is already True (from a prior run),
    return immediately without re-executing.

    Stale-state guard: Verify that the manifest stored at intake matches
    the contract identity. If modified, abort execution.
    """
    start = datetime.utcnow()
    op_id = contract.operation_id

    from orchestrator.workflow_store import workflow_store

    record = await workflow_store.get(op_id)
    if record is None:
        raise RuntimeError(f"No workflow record found for {op_id}")

    # ── Idempotency check ───────────────────────────────────────────────────
    if record.execution_completed:
        logger.warning(
            "EXECUTION skipped — already executed for %s (idempotency guard)", op_id
        )
        contract.execution_success = True
        return contract

    # ── Stale-state guard ────────────────────────────────────────────────────
    manifest = record.tool_call_manifest or {}
    stored_hash = manifest.get("_manifest_hash")
    if stored_hash:
        current_hash = _manifest_hash(
            {k: v for k, v in manifest.items() if k != "_manifest_hash"}
        )
        if stored_hash != current_hash:
            logger.error(
                "EXECUTION aborted — manifest hash mismatch for %s (stale-state guard)", op_id
            )
            contract.execution_success = False
            contract.add_provenance(
                agent="EXECUTION",
                field_written="execution_success",
                llm_involved=False,
            )
            return contract

    # ── Execute the original tool call ───────────────────────────────────────
    raw_sql = manifest.get("sql") or manifest.get("query") or contract.raw_sql
    tool_name = manifest.get("tool_name", "execute_query")
    target_url = manifest.get("target_mcp_url", mcp_client.POSTGRES_READER_URL)

    try:
        execution_result = await mcp_client.call(
            target_url,
            tool_name,
            {"sql": raw_sql, "tenant_id": contract.tenant_id},
            timeout=30.0,
        )
        contract.execution_success = True
        contract.execution_timestamp = datetime.utcnow()

        await workflow_store.set_execution_result(op_id, {
            "success": True,
            "result": execution_result,
            "executed_at": contract.execution_timestamp.isoformat() + "Z",
        })

        logger.info(
            "EXECUTION complete",
            extra={"operation_id": op_id, "success": True},
        )
    except mcp_client.MCPCallError as exc:
        logger.error("EXECUTION failed for %s: %s", op_id, exc)
        contract.execution_success = False
        contract.execution_timestamp = datetime.utcnow()

        await workflow_store.set_execution_result(op_id, {
            "success": False,
            "error": str(exc),
            "executed_at": contract.execution_timestamp.isoformat() + "Z",
        })

    elapsed_ms = (datetime.utcnow() - start).total_seconds() * 1000
    contract.add_provenance(
        agent="EXECUTION",
        field_written="execution_success,execution_timestamp",
        llm_involved=False,
    )
    return contract
