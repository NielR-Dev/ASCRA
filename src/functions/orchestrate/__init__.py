"""HTTP-triggered Azure Function: POST /api/orchestrate (T12).

Entry point called by the Logic App after dequeuing a Dynatrace SSL-expiry alert
from Service Bus. This Function:
  1. Checks the kill-switch (orchestrator_enabled).
  2. Validates the request body.
  3. Calls build_orchestrator().run(alert_json).
  4. Returns { workflow_id, state } on success.

Authentication: Entra Easy Auth / APIM (configured in Azure, not in code).
All requests without a valid Entra JWT are rejected at the APIM/Easy Auth layer.

Error envelope: { "error": { "code": "...", "message": "...", "correlation_id": "..." } }
"""
from __future__ import annotations

import json
import logging
import uuid

import azure.functions as func

from src.config import settings

logger = logging.getLogger("ssl_renewal.functions.orchestrate")

_CORRELATION_HEADER = "X-Correlation-Id"


def _error_response(
    code: str, message: str, correlation_id: str, status: int = 400
) -> func.HttpResponse:
    body = json.dumps({
        "error": {
            "code": code,
            "message": message,
            "correlation_id": correlation_id,
        }
    })
    return func.HttpResponse(body, status_code=status, mimetype="application/json")


async def main(req: func.HttpRequest) -> func.HttpResponse:
    correlation_id = req.headers.get(_CORRELATION_HEADER, str(uuid.uuid4()))
    logger.info("orchestrate: received request correlation_id=%s", correlation_id)

    # Kill-switch check (G3 / RUNBOOK)
    if not settings.orchestrator_enabled:
        logger.warning("orchestrate: agent disabled (kill-switch active)")
        return _error_response(
            "agent_disabled",
            "The SSL renewal agent is currently disabled. "
            "Contact the SSL team or check the RUNBOOK for the kill-switch procedure.",
            correlation_id,
            status=503,
        )

    # Parse request body
    try:
        body = req.get_json()
    except ValueError:
        return _error_response(
            "bad_request", "Request body must be valid JSON.", correlation_id, status=400
        )

    alert = body.get("alert") if isinstance(body, dict) else None
    if not alert:
        return _error_response(
            "missing_alert",
            "Request body must contain an 'alert' field.",
            correlation_id,
            status=400,
        )

    # Build and run the orchestrator
    try:
        from src.orchestrator.agent import build_orchestrator

        agent = build_orchestrator()
        result = await agent.run(
            f"New SSL expiry alert: {json.dumps(alert)}",
            thread_id=correlation_id,  # Use correlation_id as the MAF thread_id
        )

        response_body = json.dumps({
            "workflow_id": result.metadata.get("workflow_id", correlation_id)
            if hasattr(result, "metadata") else correlation_id,
            "state": "PARSED",
            "correlation_id": correlation_id,
            "schema_version": 1,
        })
        logger.info("orchestrate: workflow started correlation_id=%s", correlation_id)
        return func.HttpResponse(
            response_body, status_code=200, mimetype="application/json",
            headers={_CORRELATION_HEADER: correlation_id}
        )

    except Exception as exc:
        logger.exception("orchestrate: unexpected error correlation_id=%s", correlation_id)
        return _error_response(
            "internal_error",
            f"An unexpected error occurred: {type(exc).__name__}",
            correlation_id,
            status=500,
        )
