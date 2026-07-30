"""Backend: scheduled inventory scan (timer-triggered adapter).

This adapter runs on a timer (e.g. nightly via Azure Functions timer trigger)
and queries the certificate inventory to find expiring certs. It enqueues them
as a batch renewal request via the guarded core.

No business logic here. This is a thin adapter:
  1. Query cert inventory (CMDB / Azure Key Vault list).
  2. Filter for certs expiring within the configured horizon.
  3. De-duplicate against active workflow_state records.
  4. POST /api/v1/batch to the guarded core.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger("ssl_renewal.interfaces.backend.scheduled_scan")

# Default expiry horizon: 45 days ahead
EXPIRY_HORIZON_DAYS = 45


def filter_expiring(
    inventory: list[dict[str, Any]],
    horizon_days: int = EXPIRY_HORIZON_DAYS,
) -> list[dict[str, Any]]:
    """Return certs expiring within horizon_days from now.

    Each inventory item is expected to have: { "cn": str, "san": list[str],
    "owning_application": str, "not_after": str (ISO 8601) }
    """
    now = datetime.now(timezone.utc)
    cutoff = now + timedelta(days=horizon_days)
    expiring = []
    for cert in inventory:
        not_after_str = cert.get("not_after", "")
        if not not_after_str:
            continue
        try:
            not_after = datetime.fromisoformat(not_after_str.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("scheduled_scan: unparseable not_after '%s' for cn=%s",
                           not_after_str, cert.get("cn", "unknown"))
            continue
        if not_after < cutoff:
            expiring.append(cert)
    return expiring
