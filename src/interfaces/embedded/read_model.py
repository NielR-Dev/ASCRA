"""Embedded: read model — projections of workflow_state and batch for dashboards.

This module provides read-only queries on workflow state. It is the data source
for the Embedded mode (dashboard suggestions, nudge cards).

Constraints (§2.1b):
  - Read-only: this module never writes to Cosmos or calls any state-mutating tool.
  - The Managed Identity for this module has only Cosmos Built-in Data Reader role.
  - Suggestions generated here are advisory — accepting one emits a Direct or Backend
    request through the guarded core, not a direct tool call.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("ssl_renewal.interfaces.embedded.read_model")

# Cert expiry horizon for proactive suggestions (days)
PROACTIVE_HORIZON_DAYS = 30


def build_renewal_funnel_summary(workflows: list[dict[str, Any]]) -> dict[str, Any]:
    """Build a renewal funnel summary for the Azure Workbook dashboard.

    Returns counts by state and in-flight vs terminal.
    """
    counts: dict[str, int] = {}
    for wf in workflows:
        state = wf.get("state", "UNKNOWN")
        counts[state] = counts.get(state, 0) + 1

    terminal_states = {"COMPLETE", "REJECTED", "FAILED"}
    in_flight = sum(v for k, v in counts.items() if k not in terminal_states)
    return {
        "total": len(workflows),
        "in_flight": in_flight,
        "by_state": counts,
    }


def build_proactive_suggestions(
    inventory: list[dict[str, Any]],
    active_workflows: list[dict[str, Any]],
    horizon_days: int = PROACTIVE_HORIZON_DAYS,
) -> list[dict[str, Any]]:
    """Return suggestion objects for the Embedded dashboard.

    A suggestion is advisory — it never triggers a renewal directly.
    The dashboard renders an Accept button that emits a POST /api/v1/batch
    through the guarded core.

    Returns list of: { kind, title, rationale, action_ref }
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=horizon_days)

    # Build set of CNs already in active workflows (no duplicate suggestions)
    active_cns = {wf.get("cn", "").lower() for wf in active_workflows}

    expiring = []
    for cert in inventory:
        cn = cert.get("cn", "")
        not_after_str = cert.get("not_after", "")
        if not cn or not not_after_str or cn.lower() in active_cns:
            continue
        try:
            not_after = datetime.fromisoformat(not_after_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if not_after < cutoff:
            expiring.append(cert)

    if not expiring:
        return []

    owners = list({c.get("owning_application", "unknown") for c in expiring})
    suggestion = {
        "kind": "renewal_wave",
        "title": f"{len(expiring)} certificate(s) expire in the next {horizon_days} days",
        "rationale": f"Affected applications: {', '.join(owners[:5])}",
        "action_ref": {
            "type": "batch_renew",
            "alert_cns": [c.get("cn") for c in expiring],
            "endpoint": "POST /api/v1/batch",
        },
    }
    return [suggestion]
