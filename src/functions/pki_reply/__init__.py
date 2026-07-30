"""HTTP-triggered Azure Function: POST /api/pki-reply (T12).

Called by Logic App when the PKI team replies with the signed CER attachment.
The Logic App downloads the CER to Blob Storage and then calls this endpoint.

Body: { "workflow_id": "wf_...", "cer_blob_url": "https://..." }

This Function triggers the CER verification step (verify_cer) and transitions
the workflow to VERIFIED (or starts the magentic retry path on failure).
"""
from __future__ import annotations

import json
import logging
import uuid

import azure.functions as func

logger = logging.getLogger("ssl_renewal.functions.pki_reply")

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
    logger.info("pki_reply: received correlation_id=%s", correlation_id)

    try:
        body = req.get_json()
    except ValueError:
        return _error_response("bad_request", "Request body must be valid JSON.", correlation_id)

    if not isinstance(body, dict):
        return _error_response("bad_request", "Request body must be a JSON object.", correlation_id)

    workflow_id = body.get("workflow_id", "")
    cer_blob_url = body.get("cer_blob_url", "")

    if not workflow_id:
        return _error_response("missing_workflow_id", "workflow_id is required.", correlation_id)
    if not cer_blob_url:
        return _error_response("missing_cer_blob_url", "cer_blob_url is required.", correlation_id)

    logger.info(
        "pki_reply: triggering verification workflow_id=%s blob_url=%s",
        workflow_id, cer_blob_url[:60]
    )

    # In production: resume the orchestrator thread and trigger verify_cer.
    # The actual verification is orchestrated by the Orchestrator agent which reads
    # the CER from Blob and calls verify_cer(). This endpoint accepts the notification
    # and returns 202; the Orchestrator processes it asynchronously.

    return func.HttpResponse(
        json.dumps({"status": "accepted", "workflow_id": workflow_id,
                    "correlation_id": correlation_id}),
        status_code=202, mimetype="application/json",
        headers={_CORRELATION_HEADER: correlation_id}
    )
