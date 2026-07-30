"""Embedded: suggestion service — read-only suggestions for the dashboard.

This service wraps the read model and exposes the suggestion API
(GET /api/v1/suggestions → [{kind, cn, rationale, action_ref}]).

Constraint: this service holds only read-only Cosmos data-plane role.
It is a pure read surface — no state mutations, no side effects.
"""
from __future__ import annotations

import logging
from typing import Any

from src.interfaces.embedded.read_model import build_proactive_suggestions

logger = logging.getLogger("ssl_renewal.interfaces.embedded.suggestion_service")


async def get_suggestions(
    inventory: list[dict[str, Any]],
    active_workflows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Return read-only suggestions for the Embedded dashboard.

    This is the backend for GET /api/v1/suggestions.
    Suggestions are data — they never trigger renewals directly.
    """
    suggestions = build_proactive_suggestions(inventory, active_workflows)
    logger.debug(
        "suggestion_service: returning %d suggestions from %d inventory items",
        len(suggestions), len(inventory)
    )
    return suggestions
