"""Direct: web console API adapter.

Thin adapter for the operator web console. Provides endpoint mapping
and authorization scope checks. No business logic.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ssl_renewal.interfaces.direct.web_console_api")

# Role definitions (aligned with Entra group membership)
ROLE_VIEWER = "ssl-renewal.viewer"
ROLE_OPERATOR = "ssl-renewal.operator"
ROLE_APPROVER = "ssl-renewal.approver"

# Endpoint → minimum required role mapping
ENDPOINT_ROLES: dict[str, str] = {
    "GET /api/v1/workflows/{id}": ROLE_VIEWER,
    "GET /api/v1/batches/{id}": ROLE_VIEWER,
    "GET /api/v1/status": ROLE_VIEWER,
    "GET /api/v1/approvals": ROLE_APPROVER,
    "GET /api/v1/suggestions": ROLE_VIEWER,
    "POST /api/v1/renew": ROLE_OPERATOR,
    "POST /api/v1/batch": ROLE_OPERATOR,
}


def check_role(endpoint: str, user_roles: list[str]) -> bool:
    """Return True if the user has the required role for the endpoint.

    Authorization is enforced here (adapter) and also at the Azure Function layer
    (Easy Auth + Entra app roles). Defense-in-depth.
    """
    required = ENDPOINT_ROLES.get(endpoint)
    if required is None:
        logger.warning("web_console_api: unknown endpoint '%s'", endpoint)
        return False
    return required in user_roles
