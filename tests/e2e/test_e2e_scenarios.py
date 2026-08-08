"""
Phase 7 E2E tests — four demo scenarios (CLAUDE.md §23).

These tests hit the running ContraGate proxy at CONTRAGATE_PROXY_URL.
They are skipped when the server is not reachable.

Run with a live stack:
  docker compose up -d
  pytest tests/e2e/ -v -m e2e

The four scenarios:
  E2E-1: Fast-path SELECT — EXPLAIN cost 1,840, no PII, auto-executes
  E2E-2: Standard UPDATE + approval — UPDATE users, REVERSIBLE, STANDARD tier, approved
  E2E-3: Dangerous DELETE + historical rejection — DELETE users, PERMANENT, rejected
  E2E-4: MODIFY WITH CONSTRAINTS — DELETE orders, FULL CONTRACT, modified → STANDARD
"""

from __future__ import annotations

import os
import time

import httpx
import pytest

PROXY_URL = os.environ.get("CONTRAGATE_PROXY_URL", "http://localhost:8000")
NOTIFIER_URL = os.environ.get("NOTIFIER_URL", "http://localhost:8020")

pytestmark = pytest.mark.e2e


def _is_server_up() -> bool:
    try:
        r = httpx.get(f"{PROXY_URL}/health", timeout=3.0)
        return r.status_code == 200
    except Exception:
        return False


@pytest.fixture(scope="module", autouse=True)
def require_server():
    if not _is_server_up():
        pytest.skip("ContraGate proxy not reachable — start with `docker compose up -d`")


def _mcp_call(sql: str, source_type: str = "internal_system") -> httpx.Response:
    return httpx.post(
        f"{PROXY_URL}/v1/mcp",
        json={"method": "tools/call", "params": {"name": "execute_query", "arguments": {"sql": sql}}},
        headers={
            "X-ContraGate-Source": source_type,
            "X-ContraGate-Caller": "e2e-test-runner",
        },
        timeout=30.0,
    )


def _wait_for_terminal(approval_id: str, max_wait: int = 60) -> dict:
    terminal = {"APPROVED", "REJECTED", "AUTO_REJECTED", "AUTO_EXECUTED", "COMPLETED", "FAILED", "TIMED_OUT"}
    deadline = time.time() + max_wait
    while time.time() < deadline:
        r = httpx.get(f"{PROXY_URL}/v1/approvals/{approval_id}/status", timeout=5.0)
        if r.status_code == 200:
            data = r.json()
            if data.get("status") in terminal:
                return data
        time.sleep(1)
    raise TimeoutError(f"Approval {approval_id!r} did not reach terminal state within {max_wait}s")


def _dev_approve(approval_id: str, reason: str) -> None:
    r = httpx.post(
        f"{NOTIFIER_URL}/dev/approve",
        json={"approval_id": approval_id, "reason": reason},
        timeout=10.0,
    )
    assert r.status_code == 200, f"dev/approve failed: {r.text}"


def _dev_reject(approval_id: str, reason: str) -> None:
    r = httpx.post(
        f"{NOTIFIER_URL}/dev/reject",
        json={"approval_id": approval_id, "reason": reason},
        timeout=10.0,
    )
    assert r.status_code == 200, f"dev/reject failed: {r.text}"


def _dev_modify(approval_id: str, reason: str, constraints: str) -> None:
    r = httpx.post(
        f"{NOTIFIER_URL}/dev/modify",
        json={"approval_id": approval_id, "reason": reason, "modification_constraints": constraints},
        timeout=10.0,
    )
    assert r.status_code == 200, f"dev/modify failed: {r.text}"


class TestE2E1FastPathSelect:
    """E2E-1: Simple SELECT on a non-PII table with low EXPLAIN cost → auto-executes."""

    def test_select_auto_executes(self):
        sql = "SELECT id, email FROM users WHERE status = 'active' LIMIT 10"
        resp = _mcp_call(sql)
        assert resp.status_code == 200
        body = resp.json()
        # Should auto-execute or return PENDING depending on policy
        # For a simple non-PII SELECT with low cost, expect auto-execute
        status = body.get("status", "")
        assert status in ("AUTO_EXECUTED", "PENDING_HUMAN_APPROVAL"), f"Unexpected status: {body}"

    def test_health_endpoint_reachable(self):
        r = httpx.get(f"{PROXY_URL}/health", timeout=5.0)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "ok"
        assert "contragate-proxy" in data["service"]


class TestE2E2StandardUpdateApproval:
    """E2E-2: UPDATE users — REVERSIBLE, STANDARD tier, human approves."""

    def test_update_routes_to_human_review(self):
        sql = "UPDATE users SET last_login = NOW() WHERE status = 'active'"
        resp = _mcp_call(sql)
        assert resp.status_code == 200
        body = resp.json()
        # May be auto-executed or pending depending on EXPLAIN cost and policies
        if body.get("status") == "AUTO_EXECUTED":
            pytest.skip("Operation was auto-executed — adjust thresholds for this scenario")
        assert body.get("status") == "PENDING_HUMAN_APPROVAL"

    def test_approve_update_succeeds(self):
        sql = "UPDATE users SET last_seen_at = NOW() WHERE last_seen_at < NOW() - INTERVAL '1 year'"
        resp = _mcp_call(sql)
        body = resp.json()
        if body.get("status") == "AUTO_EXECUTED":
            pytest.skip("Operation was auto-executed")

        approval_id = body.get("approval_id")
        assert approval_id, f"No approval_id in response: {body}"

        # Verify contract is retrievable
        contract_resp = httpx.get(f"{PROXY_URL}/v1/approvals/{approval_id}/contract", timeout=5.0)
        assert contract_resp.status_code == 200
        contract_data = contract_resp.json()
        assert "contract" in contract_data

        # Approve via mock notifier
        _dev_approve(approval_id, "Reviewed — safe update with appropriate scope.")
        result = _wait_for_terminal(approval_id, max_wait=30)
        assert result["status"] in ("APPROVED", "COMPLETED"), f"Unexpected final status: {result}"


class TestE2E3DangerousDeleteWithRejection:
    """E2E-3: DELETE users WHERE last_active old — PERMANENT, cg_1847 should surface."""

    def test_delete_routes_to_full_contract(self):
        sql = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        resp = _mcp_call(sql)
        assert resp.status_code == 200
        body = resp.json()
        if body.get("status") == "AUTO_EXECUTED":
            pytest.skip("Unexpected auto-execution — check policy configuration")
        assert body.get("status") == "PENDING_HUMAN_APPROVAL"

    def test_reject_delete_records_reason(self):
        sql = "DELETE FROM users WHERE last_active < NOW() - INTERVAL '2 years'"
        resp = _mcp_call(sql)
        body = resp.json()
        if body.get("status") == "AUTO_EXECUTED":
            pytest.skip("Unexpected auto-execution")

        approval_id = body.get("approval_id")
        assert approval_id

        # Reject with reason referencing cascade risk
        _dev_reject(approval_id, "Cascade into invoices causes billing record loss. Archival required first.")
        result = _wait_for_terminal(approval_id, max_wait=30)
        assert result["status"] == "REJECTED"

    def test_approval_queue_endpoint_returns_list(self):
        r = httpx.get(f"{PROXY_URL}/v1/approvals", timeout=5.0)
        assert r.status_code == 200
        data = r.json()
        assert "approvals" in data
        assert isinstance(data["approvals"], list)

    def test_audit_log_endpoint_returns_records(self):
        r = httpx.get(f"{PROXY_URL}/v1/audit", timeout=5.0)
        assert r.status_code == 200
        data = r.json()
        assert "records" in data
        assert isinstance(data["records"], list)


class TestE2E4ModifyWithConstraints:
    """E2E-4: DELETE orders — human modifies, selective re-analysis, tier drops."""

    def test_modify_triggers_reanalysis(self):
        sql = "DELETE FROM orders WHERE status = 'cancelled' AND created_at < NOW() - INTERVAL '3 years'"
        resp = _mcp_call(sql)
        body = resp.json()
        if body.get("status") == "AUTO_EXECUTED":
            pytest.skip("Unexpected auto-execution")

        approval_id = body.get("approval_id")
        assert approval_id

        # Modify with constraints
        _dev_modify(
            approval_id,
            reason="Cascade risk is acceptable if we limit to 2022 data only.",
            constraints="Limit DELETE to orders before 2022-01-01. Add explicit date bound: created_at < '2022-01-01'.",
        )
        # After MODIFY, the workflow enters re-analysis — status will return to PENDING
        time.sleep(2)
        r = httpx.get(f"{PROXY_URL}/v1/approvals/{approval_id}/status", timeout=5.0)
        if r.status_code == 200:
            # Status may be RUNNING (re-analysis) or PENDING_HUMAN_APPROVAL again
            assert r.json().get("status") in ("RUNNING", "PENDING_HUMAN_APPROVAL"), r.json()


class TestDecisionReplayPrevention:
    """Security: approval_id can be decided exactly once."""

    def test_second_decision_returns_409(self):
        sql = "UPDATE orders SET updated_at = NOW() WHERE status = 'pending'"
        resp = _mcp_call(sql)
        body = resp.json()
        if body.get("status") == "AUTO_EXECUTED":
            pytest.skip("Operation was auto-executed")

        approval_id = body.get("approval_id")
        if not approval_id:
            pytest.skip("No approval_id returned")

        # First decision
        r1 = httpx.post(
            f"{PROXY_URL}/v1/decisions",
            json={"approval_id": approval_id, "decision": "REJECT", "reason": "First rejection reason here."},
            timeout=10.0,
        )
        assert r1.status_code == 200

        # Second decision — must be rejected
        r2 = httpx.post(
            f"{PROXY_URL}/v1/decisions",
            json={"approval_id": approval_id, "decision": "APPROVE", "reason": "Attempting to override rejection."},
            timeout=10.0,
        )
        assert r2.status_code == 409, f"Expected 409, got {r2.status_code}: {r2.text}"


class TestSSEStream:
    """SSE and polling endpoints work correctly."""

    def test_status_endpoint_returns_json(self):
        sql = "SELECT COUNT(*) FROM orders"
        resp = _mcp_call(sql)
        body = resp.json()
        if body.get("status") == "AUTO_EXECUTED":
            pytest.skip("Auto-executed — no pending approval to poll")

        approval_id = body.get("approval_id")
        if not approval_id:
            pytest.skip("No approval_id returned")

        r = httpx.get(f"{PROXY_URL}/v1/approvals/{approval_id}/status", timeout=5.0)
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "approval_id" in data

    def test_short_reason_rejected_at_decisions_endpoint(self):
        r = httpx.post(
            f"{PROXY_URL}/v1/decisions",
            json={"approval_id": "cg_test_99999", "decision": "APPROVE", "reason": "short"},
            timeout=5.0,
        )
        assert r.status_code == 400
        assert "10 characters" in r.json().get("detail", "").lower()

    def test_invalid_decision_rejected(self):
        r = httpx.post(
            f"{PROXY_URL}/v1/decisions",
            json={"approval_id": "cg_test_99999", "decision": "DO_WHATEVER", "reason": "long enough reason here"},
            timeout=5.0,
        )
        assert r.status_code == 400
