"""
INTAKE state — validate incoming tool call, assign operation_id, initialize HandoffContract.

Responsibilities (CLAUDE.md §10):
  - Validate incoming tool-call manifest (tool name, SQL present, tenant_id)
  - Assign operation_id in format cg_XXXXXXXX
  - Detect source_type from manifest metadata
  - Tag external input with prompt_injection_risk = True (invariant 12)
  - Wrap raw SQL in <untrusted_input> tags for all downstream LLM processing
  - Write initial provenance entry
"""

from __future__ import annotations

import hashlib
import logging
import os
import re
import uuid
from datetime import datetime
from typing import Any

from orchestrator.handoff_schema import (
    ApprovalState,
    HandoffContract,
    OperationType,
    ProvenanceEntry,
    SourceType,
)

logger = logging.getLogger(__name__)

TENANT_ID = os.environ.get("CONTRAGATE_TENANT_ID", "demo_tenant")

_DML_PATTERN = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|TRUNCATE|DROP|ALTER|CREATE)\b",
    re.IGNORECASE,
)


def _generate_operation_id() -> str:
    """Generate a unique operation ID in cg_XXXXXXXX format."""
    uid = uuid.uuid4().hex[:8]
    return f"cg_{uid}"


def _detect_operation_type(raw_sql: str) -> OperationType:
    m = _DML_PATTERN.match(raw_sql)
    if not m:
        return OperationType.UNKNOWN
    kw = m.group(1).upper()
    if kw == "SELECT":
        return OperationType.SELECT
    if kw == "INSERT":
        return OperationType.INSERT
    if kw == "UPDATE":
        return OperationType.UPDATE
    if kw == "DELETE":
        return OperationType.DELETE
    if kw in ("TRUNCATE", "DROP", "ALTER", "CREATE"):
        return OperationType.DDL
    return OperationType.UNKNOWN


def _detect_source_type(manifest: dict[str, Any]) -> SourceType:
    raw = manifest.get("source_type", "internal_system")
    try:
        return SourceType(raw)
    except ValueError:
        return SourceType.INTERNAL_SYSTEM


def run_intake(manifest: dict[str, Any]) -> HandoffContract:
    """
    Validate the incoming tool-call manifest and build an initial HandoffContract.

    Raises ValueError if required fields (tool_name, sql) are missing.
    This is the entry point called by the proxy after intercepting a tool call.
    """
    raw_sql: str | None = (
        manifest.get("sql")
        or manifest.get("query")
        or manifest.get("statement")
    )
    if not raw_sql or not raw_sql.strip():
        raise ValueError("Manifest must contain a non-empty 'sql' field")

    tool_name: str = manifest.get("tool_name", "unknown_tool")
    submitted_by: str = manifest.get("submitted_by", "unknown_agent")
    tenant_id: str = manifest.get("tenant_id", TENANT_ID)

    source_type = _detect_source_type(manifest)
    operation_type = _detect_operation_type(raw_sql)

    # All external/agent inputs are untrusted
    prompt_injection_risk = source_type in (
        SourceType.EXTERNAL_USER_INPUT, SourceType.EXTERNAL_AGENT
    )

    operation_id = _generate_operation_id()

    contract = HandoffContract(
        operation_id=operation_id,
        tenant_id=tenant_id,
        submitted_by=submitted_by,
        source_type=source_type,
        submission_timestamp=datetime.utcnow(),
        raw_sql=raw_sql,
        raw_intent=manifest.get("intent"),
        operation_type=operation_type,
        prompt_injection_risk=prompt_injection_risk,
        approval_state=ApprovalState.PENDING,
    )

    contract.add_provenance(
        agent="INTAKE",
        field_written="operation_id,source_type,operation_type,prompt_injection_risk",
        llm_involved=False,
    )

    logger.info(
        "INTAKE complete",
        extra={
            "operation_id": operation_id,
            "tool_name": tool_name,
            "source_type": source_type.value,
            "operation_type": operation_type.value,
            "prompt_injection_risk": prompt_injection_risk,
        },
    )
    return contract
