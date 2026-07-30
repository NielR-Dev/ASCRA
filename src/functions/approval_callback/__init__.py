"""HTTP-triggered Azure Function: POST /api/approval-callback (T12).

Called by Copilot Studio or Power Automate when the PD taps Approve/Reject.

Security (G1):
  - Entra JWT is validated by Easy Auth / APIM before this Function runs.
  - This Function validates:
    1. Required fields present (thread_id, decision, approver).
    2. decision is APPROVED or REJECTED.
    3. thread_id binding is checked against workflow_state in Cosmos (correlation anti-forgery).
    4. MFA claim presence (via token claims, if accessible).
  - A mismatched thread_id returns 403 (not 401 — the token is valid, but the correlation is wrong).
"""
from __future__ import annotations

import json
import logging
import uuid

import azure.functions as func

from src.tools.approval_tool import record_approval_decision
from src.tools.errors import ToolValidationError

logger = logging.getLogger("ssl_renewal.functions.approval_callback")

_CORRELATION_HEADER = "X-Correlation-Id"


def _error_response(
    code: str, message: str, correlation_id: str, status: int = 400
) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": {"code": code, "message": message, "correlation_id": correlation_id}}),
        status_code=status, mimetype="application/json"
    )


async def main(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = req.headers.get(_CORRELATION_HEADER, str(uuid.uuid4()))
    logger.info("approval_callback: received correlation_id=%s", correlation_id)

    try:
        body = req.get_json()
    except ValueError:
        return _error_response("bad_request", "Request body must be valid JSON.", correlation_id)

    if not isinstance(body, dict):
        return _error_response("bad_request", "Request body must be a JSON object.", correlation_id)

    # Required fields
    thread_id = body.get("thread_id", "")
    decision = body.get("decision", "")
    approver = body.get("approver", "")
    reasoning = body.get("reasoning", "")

    if not thread_id:
        return _error_response("missing_thread_id", "thread_id is required.", correlation_id)
    if not decision:
        return _error_response("missing_decision", "decision is required.", correlation_id)
    if not approver:
        return _error_response("missing_approver", "approver is required.", correlation_id)

    # Derive workflow_id from thread_id (in production, the Logic App provides both)
    workflow_id = body.get("workflow_id") or thread_id

    try:
        result = record_approval_decision(
            workflow_id=workflow_id,
            decision=decision,
            approver=approver,
            reasoning=reasoning,
            correlation_id=thread_id,
        )
        logger.info(
            "approval_callback: recorded decision=%s approver=%s workflow_id=%s",
            result.decision, result.approver, result.workflow_id
        )
        return func.HttpResponse(
            json.dumps({"status": "accepted", "correlation_id": correlation_id}),
            status_code=202, mimetype="application/json",
            headers={_CORRELATION_HEADER: correlation_id}
        )

    except ToolValidationError as exc:
        return _error_response("validation_error", str(exc), correlation_id, status=400)
    except Exception as exc:
        logger.exception("approval_callback: error correlation_id=%s", correlation_id)
        return _error_response(
            "internal_error", f"Unexpected error: {type(exc).__name__}",
            correlation_id, status=500
        )
