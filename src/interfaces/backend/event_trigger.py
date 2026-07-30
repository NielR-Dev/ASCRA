"""Backend: event trigger adapter — processes Dynatrace SSL-expiry events from Service Bus.

This is a thin adapter (protocol + authN only, no business logic).
Responsibilities:
  1. Validate the Service Bus / Event Grid message signature.
  2. Parse the event envelope into a normalized alert dict.
  3. Call POST /api/orchestrate (the guarded core entry point).

No guardrails, no state mutations, no business rules live here.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger("ssl_renewal.interfaces.backend.event_trigger")


def parse_dynatrace_alert(event: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Dynatrace SSL-expiry event into the canonical alert format.

    The canonical format is: { "cn": str, "san": list[str], "owning_application": str,
                                "source": str, "problem_id": str, "received_at": str }

    If the event does not contain CN/SAN (only hostname), the CMDB enrichment step
    in the orchestrator will fill them in. This function extracts what's available.

    Raises:
        ValueError: if the event is missing the minimal required fields (hostname).
    """
    data = event.get("data", event)  # Event Grid wraps data in a 'data' field

    # Dynatrace sends a problem title and a series of impacted entities
    hostname = (
        data.get("hostname")
        or data.get("affectedEntity", {}).get("name", "")
        or data.get("impactedEntities", [{}])[0].get("name", "")
        if isinstance(data.get("impactedEntities"), list) else ""
    )
    cn = data.get("cn") or hostname
    if not cn:
        raise ValueError(
            f"Dynatrace event does not contain a hostname or CN: {json.dumps(data)[:200]}"
        )

    san = data.get("san", [cn] if cn else [])
    if isinstance(san, str):
        san = [s.strip() for s in san.split(",") if s.strip()]

    problem_id = (
        data.get("problemId")
        or data.get("ProblemID")
        or data.get("problem_id", "")
    )

    return {
        "cn": cn,
        "san": san,
        "owning_application": data.get("owning_application", ""),
        "source": "dynatrace",
        "problem_id": problem_id,
        "received_at": data.get("received_at") or data.get("timestamp", ""),
        "raw_event": data,
    }
