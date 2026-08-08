"""
notifier MCP server — Slack integration (mock for local dev).

Local dev behavior (USE_MOCK_NOTIFIER=true):
  - Prints approval request to stdout in a readable format
  - Exposes POST /dev/approve and POST /dev/reject for programmatic decisions
  - Decisions are posted back to the proxy via HTTP callback

Production behavior (USE_MOCK_NOTIFIER=false):
  - Sends formatted message to SLACK_APPROVAL_CHANNEL
  - Three Slack action buttons: View Full Contract, Approve (opens modal), Reject (opens modal)
  - Modals capture reason text (minimum 10 characters)
  - Slack webhooks decision back to proxy

Tools exposed:
  send_approval_request — Deliver contract summary to approver
  record_decision       — Record approval decision (called by Slack webhook handler)
"""

from __future__ import annotations

import json
import os
from datetime import datetime

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from mcp_servers.base.server_base import ContraGateMCPServer


PORT = int(os.environ.get("NOTIFIER_PORT", "8020"))
USE_MOCK = os.environ.get("USE_MOCK_NOTIFIER", "true").lower() == "true"
PROXY_CALLBACK_URL = os.environ.get(
    "PROXY_CALLBACK_URL", "http://contragate-proxy:8000/v1/decisions"
)
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
SLACK_APPROVAL_CHANNEL = os.environ.get("SLACK_APPROVAL_CHANNEL", "#contragate-approvals")

server = ContraGateMCPServer("notifier", port=PORT)

# In-memory pending approvals (maps approval_id -> contract summary)
_pending: dict[str, dict] = {}


@server.tool("send_approval_request")
def send_approval_request(
    approval_id: str,
    operation_summary: str,
    risk_tier: str,
    primary_table: str,
    estimated_rows: int,
    reversibility: str,
    historical_rejection_count: int,
    ui_url: str,
    poll_url: str,
) -> dict:
    """
    Deliver the consequence contract summary to the human approver.

    In local dev: prints to stdout and stores in _pending.
    In production: sends a Slack message with action buttons.
    """
    contract_info = {
        "approval_id": approval_id,
        "operation_summary": operation_summary,
        "risk_tier": risk_tier,
        "primary_table": primary_table,
        "estimated_rows": estimated_rows,
        "reversibility": reversibility,
        "historical_rejection_count": historical_rejection_count,
        "ui_url": ui_url,
        "poll_url": poll_url,
        "received_at": datetime.utcnow().isoformat() + "Z",
    }
    _pending[approval_id] = contract_info

    if USE_MOCK:
        _print_mock_approval(contract_info)
        return {"sent": True, "method": "mock_terminal"}
    else:
        return _send_slack_approval(contract_info)


def _print_mock_approval(info: dict) -> None:
    separator = "=" * 70
    print(f"\n{separator}")
    print("ContraGate — Human Approval Required")
    print(separator)
    print(f"  Approval ID  : {info['approval_id']}")
    print(f"  Risk Tier    : {info['risk_tier']}")
    print(f"  Primary Table: {info['primary_table']}")
    print(f"  Est. Rows    : {info['estimated_rows']:,}")
    print(f"  Reversibility: {info['reversibility']}")
    if info["historical_rejection_count"] > 0:
        print(f"  ⚠  {info['historical_rejection_count']} historical rejection(s) for similar operations")
    print(f"\n  Operation: {info['operation_summary']}")
    print(f"\n  Review at: {info['ui_url']}")
    print(f"\n  To approve programmatically:")
    print(f"    POST http://localhost:{PORT}/dev/approve")
    print(f'    {{"approval_id": "{info["approval_id"]}", "reason": "your reason here"}}')
    print(f"\n  To reject programmatically:")
    print(f"    POST http://localhost:{PORT}/dev/reject")
    print(f'    {{"approval_id": "{info["approval_id"]}", "reason": "your reason here"}}')
    print(separator + "\n")


def _send_slack_approval(info: dict) -> dict:
    """Send approval request to Slack channel."""
    if not SLACK_BOT_TOKEN:
        raise RuntimeError("SLACK_BOT_TOKEN not set for production notifier")

    message = {
        "channel": SLACK_APPROVAL_CHANNEL,
        "text": f"ContraGate Review Required — {info['risk_tier']}",
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": "ContraGate — Approval Required"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"*{info['operation_summary']}*\n"
                        f"Table: `{info['primary_table']}` | Rows: {info['estimated_rows']:,} | "
                        f"Tier: `{info['risk_tier']}` | Reversibility: `{info['reversibility']}`"
                    ),
                },
            },
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "View Full Contract"},
                        "url": info["ui_url"],
                    },
                ],
            },
        ],
    }

    resp = httpx.post(
        "https://slack.com/api/chat.postMessage",
        headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
        json=message,
        timeout=10.0,
    )
    data = resp.json()
    return {"sent": data.get("ok", False), "method": "slack", "ts": data.get("ts")}


@server.tool("record_decision")
def record_decision(
    approval_id: str,
    decision: str,
    reason: str,
) -> dict:
    """
    Record a human decision and post it back to the proxy.
    Called by the Slack webhook handler or local dev POST endpoints.
    """
    if len(reason) < 10:
        raise ValueError("Decision reason must be at least 10 characters")

    if decision not in ("APPROVE", "REJECT", "MODIFY", "REQUEST_PREREQUISITE"):
        raise ValueError(f"Invalid decision: {decision}")

    _pending.pop(approval_id, None)

    # Post decision back to the proxy workflow store
    payload = {
        "approval_id": approval_id,
        "decision": decision,
        "reason": reason,
        "timestamp": datetime.utcnow().isoformat() + "Z",
    }
    try:
        httpx.post(PROXY_CALLBACK_URL, json=payload, timeout=10.0)
    except Exception as exc:
        server.logger.error("Failed to post decision to proxy", error=str(exc))

    return {"recorded": True, "approval_id": approval_id, "decision": decision}


# ─── Local dev endpoints ───────────────────────────────────────────────────────
# These are NOT MCP tools — they are HTTP endpoints on the notifier service
# for programmatic approval/rejection during local development and testing.

class DevDecisionPayload(BaseModel):
    approval_id: str
    reason: str
    modification_constraints: str | None = None


@server.app.post("/dev/approve")
async def dev_approve(body: DevDecisionPayload) -> dict:
    """Local dev endpoint: approve a pending operation."""
    return record_decision(body.approval_id, "APPROVE", body.reason)


@server.app.post("/dev/reject")
async def dev_reject(body: DevDecisionPayload) -> dict:
    """Local dev endpoint: reject a pending operation."""
    return record_decision(body.approval_id, "REJECT", body.reason)


@server.app.post("/dev/modify")
async def dev_modify(body: DevDecisionPayload) -> dict:
    """Local dev endpoint: modify-with-constraints a pending operation."""
    if not body.modification_constraints:
        raise HTTPException(400, "modification_constraints required for MODIFY")
    return record_decision(body.approval_id, "MODIFY", body.reason)


@server.app.get("/dev/pending")
async def dev_list_pending() -> dict:
    """Local dev endpoint: list all pending approvals."""
    return {"pending": list(_pending.values())}


if __name__ == "__main__":
    server.run()
