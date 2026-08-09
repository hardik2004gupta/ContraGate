"""
Slack webhook handler for ContraGate.

When a Slack block-kit button action fires (View, Approve modal, Reject modal),
Slack POSTs to this endpoint. This handler:
  1. Verifies the Slack request signing secret
  2. Parses the payload (block_actions or view_submission)
  3. Extracts approval_id, decision, and reason
  4. Forwards to the workflow store via the existing POST /v1/decisions logic

Security: Slack signs every request with a HMAC-SHA256 of the raw body using
SLACK_SIGNING_SECRET. We verify before trusting any payload content.

Local dev: USE_MOCK_NOTIFIER=true → Slack verification is skipped; decisions
arrive via POST /dev/approve and /dev/reject on the notifier service instead.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import time
import urllib.parse

import httpx
from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from orchestrator.handoff_schema import ApprovalState
from orchestrator.workflow_store import WorkflowStatus, workflow_store

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/slack")

SLACK_SIGNING_SECRET = os.environ.get("SLACK_SIGNING_SECRET", "")
SLACK_BOT_TOKEN = os.environ.get("SLACK_BOT_TOKEN", "")
USE_MOCK = os.environ.get("USE_MOCK_NOTIFIER", "true").lower() == "true"

# Slack replay-attack window: reject requests older than 5 minutes
_REPLAY_WINDOW_SECONDS = 300


def _verify_slack_signature(
    body: bytes,
    timestamp: str,
    signature: str,
) -> bool:
    """Return True if the Slack signature is valid for the given body and timestamp."""
    if not SLACK_SIGNING_SECRET:
        return False
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        return False
    if abs(time.time() - ts) > _REPLAY_WINDOW_SECONDS:
        return False
    basestring = f"v0:{timestamp}:{body.decode()}"
    expected = "v0=" + hmac.new(
        SLACK_SIGNING_SECRET.encode(),
        basestring.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/webhook")
async def slack_webhook(
    request: Request,
    x_slack_request_timestamp: str = Header(default=""),
    x_slack_signature: str = Header(default=""),
) -> JSONResponse:
    """
    Receive Slack interactive payload (block_actions or view_submission).
    Verify signature, extract decision, update workflow store.
    """
    raw_body = await request.body()

    # Skip signature verification in mock mode (local dev)
    if not USE_MOCK:
        if not _verify_slack_signature(raw_body, x_slack_request_timestamp, x_slack_signature):
            raise HTTPException(status_code=401, detail="Invalid Slack signature")

    # Slack sends payload as URL-encoded form: payload=<json>
    try:
        form = urllib.parse.parse_qs(raw_body.decode())
        payload_json = form.get("payload", ["{}"])[0]
        payload = json.loads(payload_json)
    except Exception as exc:
        logger.error("Failed to parse Slack payload: %s", exc)
        raise HTTPException(status_code=400, detail="Malformed Slack payload")

    payload_type = payload.get("type", "")

    if payload_type == "block_actions":
        return await _handle_block_action(payload)
    elif payload_type == "view_submission":
        return await _handle_view_submission(payload)
    else:
        logger.warning("Unknown Slack payload type: %s", payload_type)
        return JSONResponse(content={"ok": True})


async def _handle_block_action(payload: dict) -> JSONResponse:
    """Handle Slack button clicks. Approve/Reject buttons open modals via views.open."""
    actions = payload.get("actions", [])
    if not actions:
        return JSONResponse(content={"ok": True})

    action = actions[0]
    action_id = action.get("action_id", "")
    trigger_id = payload.get("trigger_id", "")

    if action_id == "view_contract":
        # Link button — Slack opens the URL automatically, nothing to do
        return JSONResponse(content={"ok": True})

    # Approve / Reject modal buttons: action_id is open_approve_modal_<id> or open_reject_modal_<id>
    if action_id.startswith("open_approve_modal_"):
        approval_id = action_id[len("open_approve_modal_"):]
        return await _open_decision_modal(trigger_id, "approve", approval_id)

    if action_id.startswith("open_reject_modal_"):
        approval_id = action_id[len("open_reject_modal_"):]
        return await _open_decision_modal(trigger_id, "reject", approval_id)

    return JSONResponse(content={"ok": True})


async def _open_decision_modal(trigger_id: str, decision: str, approval_id: str) -> JSONResponse:
    """Open a Slack modal to capture the approval/rejection reason."""
    if not SLACK_BOT_TOKEN:
        logger.warning("SLACK_BOT_TOKEN not set — cannot open modal")
        return JSONResponse(content={"ok": False, "error": "no_token"})

    title = "Approve Operation" if decision == "approve" else "Reject Operation"
    submit_label = "Confirm Approve" if decision == "approve" else "Confirm Reject"
    callback_id = f"{decision}_{approval_id}"

    modal = {
        "type": "modal",
        "callback_id": callback_id,
        "title": {"type": "plain_text", "text": title},
        "submit": {"type": "plain_text", "text": submit_label},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "reason_block",
                "label": {"type": "plain_text", "text": "Reason (minimum 10 characters)"},
                "element": {
                    "type": "plain_text_input",
                    "action_id": "reason_input",
                    "multiline": True,
                    "min_length": 10,
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Provide a clear reason for your decision…",
                    },
                },
            },
        ],
    }

    try:
        resp = httpx.post(
            "https://slack.com/api/views.open",
            headers={"Authorization": f"Bearer {SLACK_BOT_TOKEN}"},
            json={"trigger_id": trigger_id, "view": modal},
            timeout=10.0,
        )
        data = resp.json()
        if not data.get("ok"):
            logger.warning("Slack views.open failed: %s", data.get("error"))
    except Exception as exc:
        logger.error("Failed to open Slack modal: %s", exc)

    return JSONResponse(content={"ok": True})


async def _handle_view_submission(payload: dict) -> JSONResponse:
    """Handle Slack modal submission (Approve/Reject with reason)."""
    view = payload.get("view", {})
    callback_id = view.get("callback_id", "")

    # callback_id encodes: "approve_<approval_id>" or "reject_<approval_id>"
    if callback_id.startswith("approve_"):
        decision = "APPROVE"
        approval_id = callback_id[len("approve_"):]
    elif callback_id.startswith("reject_"):
        decision = "REJECT"
        approval_id = callback_id[len("reject_"):]
    else:
        logger.warning("Unknown Slack modal callback_id: %s", callback_id)
        return JSONResponse(content={"response_action": "clear"})

    # Extract reason from modal input block
    state_values = view.get("state", {}).get("values", {})
    reason = ""
    for block_values in state_values.values():
        for element_value in block_values.values():
            if element_value.get("type") == "plain_text_input":
                reason = element_value.get("value", "")
                break

    if len(reason) < 10:
        return JSONResponse(content={
            "response_action": "errors",
            "errors": {
                "reason_block": "Reason must be at least 10 characters.",
            },
        })

    approver_id = payload.get("user", {}).get("id", "slack_user")

    recorded = await workflow_store.record_decision(
        approval_id=approval_id,
        decision=decision,
        reason=reason,
        approver_id=approver_id,
    )

    if recorded:
        status_map = {"APPROVE": WorkflowStatus.APPROVED, "REJECT": WorkflowStatus.REJECTED}
        await workflow_store.update_status(approval_id, status_map[decision])
        logger.info(
            "Slack webhook decision recorded",
            extra={"approval_id": approval_id, "decision": decision},
        )
    else:
        logger.warning("Slack webhook: approval_id not found or already decided: %s", approval_id)

    return JSONResponse(content={"response_action": "clear"})
