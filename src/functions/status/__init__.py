"""HTTP-triggered Azure Function: GET /api/status (T12).

Query params: ?cn=api.prod.example.com  OR  ?workflow_id=wf_...

Returns the current workflow state, last-updated timestamp, and deep links (Jira, CHG).

Authentication: Entra Easy Auth / APIM (configured in Azure, not in code).
"""
from __future__ import annotations

import json
import logging
import uuid

import azure.functions as func

from src.config import settings

logger = logging.getLogger("ssl_renewal.functions.status")

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

    cn = req.params.get("cn", "")
    workflow_id = req.params.get("workflow_id", "")

    if not cn and not workflow_id:
        # Health check path — no params means "are you alive?"
        return func.HttpResponse(
            json.dumps({
                "status": "healthy",
                "orchestrator_enabled": settings.orchestrator_enabled,
            }),
            status_code=200,
            mimetype="application/json",
            headers={_CORRELATION_HEADER: correlation_id},
        )

    try:
        from src.persistence.cosmos_repo import CosmosRepo

        repo = CosmosRepo()
        await repo.initialise()

        doc = None
        if workflow_id:
            doc = await repo.get_workflow(workflow_id)
        elif cn:
            doc = await repo.get_workflow_by_cn(cn)

        if doc is None:
            return _error_response(
                "not_found",
                f"No workflow found for {'cn=' + cn if cn else 'workflow_id=' + workflow_id}.",
                correlation_id,
                status=404,
            )

        # Return safe subset — never include private key material or SAS URLs
        response = {
            "workflow_id": doc.get("workflow_id"),
            "state": doc.get("state"),
            "cn": doc.get("cn"),
            "san": doc.get("san"),
            "owning_application": doc.get("owning_application"),
            "updated_at": doc.get("updated_at"),
            "created_at": doc.get("created_at"),
            "jira_ticket": doc.get("csr", {}).get("jira_ticket") if doc.get("csr") else None,
            "chg_number": doc.get("change", {}).get("chg_number") if doc.get("change") else None,
            "correlation_id": correlation_id,
            "schema_version": 1,
        }

        return func.HttpResponse(
            json.dumps(response), status_code=200, mimetype="application/json",
            headers={_CORRELATION_HEADER: correlation_id}
        )

    except Exception as exc:
        logger.exception("status: error correlation_id=%s", correlation_id)
        return _error_response(
            "internal_error", f"Unexpected error: {type(exc).__name__}",
            correlation_id, status=500
        )
