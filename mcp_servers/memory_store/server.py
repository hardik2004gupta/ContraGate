"""
memory-store MCP server — pgvector tenant-scoped operation memory.

Permissions: Read-write on contragate_app.operation_memory.
Every query filters by tenant_id — no cross-tenant data access.

Tools exposed:
  store_operation         — Write a completed operation to memory
  semantic_search         — Cosine similarity search via pgvector
  filter_by_table_overlap — Jaccard similarity filter on affected table sets
  rerank_by_outcome       — Float REJECTED/ROLLED_BACK operations to top
  update_decision         — Update outcome after human decision
  update_outcome          — Update with post-execution actual rows
  get_confidence_scores   — Retrieve confidence scores for a table
  update_confidence_score — Adjust confidence score after feedback loop
  check_auto_reject_pattern — Check if pattern was recently auto-rejected

Security invariant (CLAUDE.md §22, invariant 6):
No cross-tenant memory retrieval. Every tool requires tenant_id.
Server enforces WHERE tenant_id = $1 on ALL queries.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from typing import Any

import psycopg2
import psycopg2.extras

from mcp_servers.base.server_base import ContraGateMCPServer


DATABASE_URL = os.environ.get("DATABASE_URL", "")
PORT = int(os.environ.get("MEMORY_STORE_PORT", "8012"))
JACCARD_THRESHOLD = float(os.environ.get("JACCARD_SIMILARITY_THRESHOLD", "0.3"))

server = ContraGateMCPServer("memory-store", port=PORT)
app = server.app  # uvicorn entry point


def _get_conn() -> psycopg2.extensions.connection:
    return psycopg2.connect(DATABASE_URL)


def _jaccard(a: set, b: set) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


@server.tool("store_operation")
def store_operation(
    operation_id: str,
    tenant_id: str,
    intent_summary: str,
    affected_tables: list[str],
    operation_type: str,
    estimated_rows: int,
    outcome: str,
    decision_reason: str,
    actual_rows: int | None = None,
    blast_radius_delta: float | None = None,
    reanalysis_count: int = 0,
    embedding: list[float] | None = None,
) -> dict:
    """
    Write a completed operation to the memory store.
    Called after every human decision (including auto-reject).
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            embedding_val = None
            if embedding:
                embedding_val = f"[{','.join(str(x) for x in embedding)}]"

            cur.execute(
                """
                INSERT INTO contragate_app.operation_memory (
                    operation_id, tenant_id, intent_summary, intent_embedding,
                    affected_tables, operation_type, estimated_rows, actual_rows,
                    outcome, decision_reason, blast_radius_delta, reanalysis_count,
                    submitted_at, decided_at
                ) VALUES (
                    %s, %s, %s, %s::vector,
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    NOW(), NOW()
                )
                ON CONFLICT (operation_id) DO UPDATE SET
                    outcome = EXCLUDED.outcome,
                    decision_reason = EXCLUDED.decision_reason,
                    actual_rows = EXCLUDED.actual_rows,
                    blast_radius_delta = EXCLUDED.blast_radius_delta,
                    decided_at = NOW()
                """,
                (
                    operation_id, tenant_id, intent_summary, embedding_val,
                    affected_tables, operation_type, estimated_rows, actual_rows,
                    outcome, decision_reason, blast_radius_delta, reanalysis_count,
                ),
            )
        conn.commit()
        return {"stored": True, "operation_id": operation_id}
    except Exception as exc:
        conn.rollback()
        raise
    finally:
        conn.close()


@server.tool("semantic_search")
def semantic_search(
    intent: str,
    tenant_id: str,
    top_k: int = 20,
) -> dict:
    """
    Stage 1 semantic search over operation memory.

    Phase 4: Uses pgvector cosine similarity via Anthropic/Voyage embeddings.
    Fallback: ILIKE text search when embeddings are unavailable (graceful degradation).

    Returns top_k candidates sorted by similarity descending.
    All results are scoped to tenant_id (invariant 6).
    """
    from mcp_servers.memory_store.embeddings import (
        EmbeddingUnavailableError,
        get_embedding,
    )

    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            try:
                embedding = get_embedding(intent)
                embedding_literal = f"[{','.join(str(x) for x in embedding)}]"
                # pgvector cosine similarity: <=> operator returns distance (0=identical)
                # 1 - distance = similarity score
                cur.execute(
                    """
                    SELECT
                        operation_id, intent_summary, affected_tables,
                        operation_type, estimated_rows, actual_rows,
                        outcome, decision_reason, blast_radius_delta,
                        (1 - (intent_embedding <=> %s::vector)) AS similarity_score
                    FROM contragate_app.operation_memory
                    WHERE tenant_id = %s
                      AND intent_embedding IS NOT NULL
                    ORDER BY intent_embedding <=> %s::vector
                    LIMIT %s
                    """,
                    (embedding_literal, tenant_id, embedding_literal, top_k),
                )
                rows = [dict(r) for r in cur.fetchall()]
                for r in rows:
                    r["affected_tables"] = list(r["affected_tables"]) if r["affected_tables"] else []
                    r["similarity_score"] = float(r["similarity_score"] or 0.0)
                return {"candidates": rows, "count": len(rows), "search_type": "vector"}

            except EmbeddingUnavailableError:
                # Graceful degradation: fall back to text search
                pass

            # ILIKE text fallback (no embeddings available)
            words = [w.strip() for w in intent.split() if len(w.strip()) > 3]
            if words:
                pattern = "%" + "%".join(words[:5]) + "%"
                cur.execute(
                    """
                    SELECT
                        operation_id, intent_summary, affected_tables,
                        operation_type, estimated_rows, actual_rows,
                        outcome, decision_reason, blast_radius_delta,
                        0.5 AS similarity_score
                    FROM contragate_app.operation_memory
                    WHERE tenant_id = %s
                      AND LOWER(intent_summary) ILIKE LOWER(%s)
                    ORDER BY decided_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, pattern, top_k),
                )
            else:
                cur.execute(
                    """
                    SELECT
                        operation_id, intent_summary, affected_tables,
                        operation_type, estimated_rows, actual_rows,
                        outcome, decision_reason, blast_radius_delta,
                        0.3 AS similarity_score
                    FROM contragate_app.operation_memory
                    WHERE tenant_id = %s
                    ORDER BY decided_at DESC
                    LIMIT %s
                    """,
                    (tenant_id, top_k),
                )
            rows = [dict(r) for r in cur.fetchall()]
            for r in rows:
                r["affected_tables"] = list(r["affected_tables"]) if r["affected_tables"] else []
        return {"candidates": rows, "count": len(rows), "search_type": "text"}
    finally:
        conn.close()


@server.tool("filter_by_table_overlap")
def filter_by_table_overlap(
    candidates: list[dict],
    current_tables: list[str],
    threshold: float | None = None,
) -> dict:
    """
    Filter candidates by Jaccard similarity of affected table sets.
    Retains candidates where Jaccard(current_tables, candidate_tables) >= threshold.
    """
    min_jaccard = threshold if threshold is not None else JACCARD_THRESHOLD
    current_set = set(current_tables)
    filtered = []
    for c in candidates:
        candidate_tables = set(c.get("affected_tables", []))
        j = _jaccard(current_set, candidate_tables)
        if j >= min_jaccard:
            filtered.append({**c, "jaccard_score": round(j, 4)})
    filtered.sort(key=lambda x: x["jaccard_score"], reverse=True)
    return {"filtered": filtered, "count": len(filtered)}


@server.tool("rerank_by_outcome")
def rerank_by_outcome(candidates: list[dict]) -> dict:
    """
    Rerank candidates by outcome severity score.
    REJECTED=1.0, ROLLED_BACK=0.9, high-delta=0.7, MODIFIED=0.5, APPROVED=0.1*similarity.

    Returns top 3 by rerank score. These appear in the contract's historical section.
    """
    def _score(c: dict) -> float:
        outcome = c.get("outcome", "APPROVED")
        delta = c.get("blast_radius_delta") or 0.0
        similarity = c.get("similarity_score", 0.0)
        if outcome == "REJECTED":
            return 1.0
        if outcome == "ROLLED_BACK":
            return 0.9
        if outcome in ("APPROVED", "AUTO_REJECTED") and abs(delta) > 0.2:
            return 0.7
        if outcome == "MODIFIED":
            return 0.5
        return 0.1 * similarity

    ranked = sorted(candidates, key=_score, reverse=True)
    for c in ranked:
        c["rerank_score"] = round(_score(c), 4)
    top3 = ranked[:3]
    return {"top3": top3}


@server.tool("update_decision")
def update_decision(
    operation_id: str,
    tenant_id: str,
    decision: str,
    reason: str,
    timestamp: str,
) -> dict:
    """Update the human decision for an operation already in memory."""
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE contragate_app.operation_memory
                SET outcome = %s, decision_reason = %s, decided_at = %s
                WHERE operation_id = %s AND tenant_id = %s
                """,
                (decision, reason, timestamp, operation_id, tenant_id),
            )
            updated = cur.rowcount > 0
        conn.commit()
        return {"updated": updated}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@server.tool("update_outcome")
def update_outcome(
    operation_id: str,
    tenant_id: str,
    actual_rows: int,
    execution_success: bool,
) -> dict:
    """
    Update operation with post-execution actual row count and compute accuracy delta.
    Called by the AUDIT state after execution completes.
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT estimated_rows FROM contragate_app.operation_memory "
                "WHERE operation_id = %s AND tenant_id = %s",
                (operation_id, tenant_id),
            )
            row = cur.fetchone()
            estimated = row[0] if row else 0

            delta = None
            if estimated > 0:
                delta = (actual_rows - estimated) / estimated
            elif actual_rows > 0:
                delta = float("inf")

            cur.execute(
                """
                UPDATE contragate_app.operation_memory
                SET actual_rows = %s, blast_radius_delta = %s
                WHERE operation_id = %s AND tenant_id = %s
                """,
                (actual_rows, delta, operation_id, tenant_id),
            )
            conn.commit()
        return {"updated": True, "accuracy_delta": delta}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@server.tool("get_confidence_scores")
def get_confidence_scores(
    table: str, operation_type: str, tenant_id: str
) -> dict:
    """Return the current confidence score for a (table, operation_type) pair."""
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT confidence, sample_count, last_updated
                FROM contragate_app.confidence_scores
                WHERE table_name = %s AND operation_type = %s AND tenant_id = %s
                """,
                (table, operation_type, tenant_id),
            )
            row = cur.fetchone()
        if row:
            return {
                "confidence": row["confidence"],
                "sample_count": row["sample_count"],
                "last_updated": row["last_updated"].isoformat(),
            }
        return {"confidence": 0.8, "sample_count": 0, "last_updated": None}
    finally:
        conn.close()


@server.tool("update_confidence_score")
def update_confidence_score(
    table: str, operation_type: str, delta: float, tenant_id: str
) -> dict:
    """
    Adjust the confidence score for a table/operation_type combination.
    Called by the post-execution feedback loop (CLAUDE.md §21).

    Delta is negative (score decreases) for both under and over estimates.
    Confidence is clamped to [0.1, 1.0].
    """
    conn = _get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO contragate_app.confidence_scores
                    (table_name, operation_type, confidence, sample_count, last_updated, tenant_id)
                VALUES (%s, %s, GREATEST(0.1, 0.8 + %s), 1, NOW(), %s)
                ON CONFLICT (table_name, operation_type, tenant_id) DO UPDATE SET
                    confidence = GREATEST(0.1, LEAST(1.0,
                        contragate_app.confidence_scores.confidence + %s)),
                    sample_count = contragate_app.confidence_scores.sample_count + 1,
                    last_updated = NOW()
                """,
                (table, operation_type, delta, tenant_id, delta),
            )
            cur.execute(
                "SELECT confidence FROM contragate_app.confidence_scores "
                "WHERE table_name=%s AND operation_type=%s AND tenant_id=%s",
                (table, operation_type, tenant_id),
            )
            row = cur.fetchone()
            new_conf = row[0] if row else 0.8
        conn.commit()
        return {"new_confidence": new_conf}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


@server.tool("check_auto_reject_pattern")
def check_auto_reject_pattern(
    tables: list[str],
    operation_type: str,
    tenant_id: str,
    lookback_days: int = 30,
) -> dict:
    """
    Check if an operation matching these tables and type was auto-rejected
    within the lookback window.
    """
    conn = _get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT operation_id, rejection_reason, rejected_at
                FROM contragate_app.auto_reject_patterns
                WHERE tenant_id = %s
                  AND operation_type = %s
                  AND affected_tables && %s
                  AND rejected_at > NOW() - (%s || ' days')::INTERVAL
                ORDER BY rejected_at DESC
                LIMIT 1
                """,
                (tenant_id, operation_type, tables, str(lookback_days)),
            )
            row = cur.fetchone()
        if row:
            return {
                "should_auto_reject": True,
                "matching_operation_id": row["operation_id"],
                "reason": row["rejection_reason"],
            }
        return {"should_auto_reject": False, "matching_operation_id": None, "reason": None}
    finally:
        conn.close()


if __name__ == "__main__":
    server.run()
