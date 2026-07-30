"""Direct: Slack adapter — slash command handler with HMAC-SHA256 signature verification.

Responsibilities (adapter only — no business logic):
  1. Verify X-Slack-Signature (HMAC-SHA256) — reject unsigned/replayed requests.
  2. Parse slash command + args.
  3. Map Slack user ID → Entra identity.
  4. Route to the guarded core entry point.

Verified by test_slack_signature_required.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import time
from typing import Any

logger = logging.getLogger("ssl_renewal.interfaces.direct.slack_adapter")

# Maximum age of a Slack request timestamp before it is rejected as replayed (5 minutes)
MAX_TIMESTAMP_AGE_SECONDS = 300


def verify_slack_signature(
    signing_secret: str,
    timestamp: str,
    body: str,
    signature: str,
) -> bool:
    """Verify the Slack request signature (HMAC-SHA256).

    Returns True if the signature is valid and the timestamp is fresh.
    Returns False if either check fails (reject the request).

    See: https://api.slack.com/authentication/verifying-requests-from-slack
    """
    if not signing_secret:
        logger.error("slack_adapter: SLACK_SIGNING_SECRET is not configured.")
        return False

    # 1. Reject requests with timestamps older than 5 minutes (replay protection)
    try:
        ts = int(timestamp)
    except (ValueError, TypeError):
        logger.warning("slack_adapter: invalid timestamp '%s'", timestamp)
        return False

    age = abs(time.time() - ts)
    if age > MAX_TIMESTAMP_AGE_SECONDS:
        logger.warning("slack_adapter: request timestamp too old (age=%.0fs)", age)
        return False

    # 2. Compute expected signature
    sig_basestring = f"v0:{timestamp}:{body}"
    expected = (
        "v0="
        + hmac.new(
            signing_secret.encode(),
            sig_basestring.encode(),
            hashlib.sha256,
        ).hexdigest()
    )

    # 3. Constant-time comparison (prevent timing attacks)
    if not hmac.compare_digest(expected, signature):
        logger.warning("slack_adapter: signature mismatch (request may be forged)")
        return False

    return True


def parse_slash_command(form_data: dict[str, str]) -> dict[str, Any]:
    """Parse a Slack slash command form payload into a normalized command dict.

    Returns: { "command": str, "text": str, "user_id": str, "team_id": str }
    """
    return {
        "command": form_data.get("command", ""),
        "text": form_data.get("text", "").strip(),
        "user_id": form_data.get("user_id", ""),
        "team_id": form_data.get("team_id", ""),
        "response_url": form_data.get("response_url", ""),
    }


def map_command_to_action(command: str, text: str) -> dict[str, Any] | None:
    """Map a Slack slash command to a core API action.

    Returns { "action": str, "params": dict } or None if unrecognized.
    All mutations route through the guarded core API — no direct state changes here.
    """
    if command == "/ssl-status":
        return {"action": "GET_STATUS", "params": {"lookup": text}}
    elif command == "/ssl-renew":
        return {"action": "POST_RENEW", "params": {"cn": text}}
    elif command == "/ssl-batch":
        return {"action": "POST_BATCH", "params": {"wave_description": text}}
    else:
        logger.warning("slack_adapter: unknown command '%s'", command)
        return None
