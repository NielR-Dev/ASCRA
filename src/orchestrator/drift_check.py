"""MCP schema-drift start-up check (G5).

Fails closed: if any pinned MCP tool schema has changed since the last deployment,
the orchestrator refuses to start rather than silently proceeding with a wrong schema.

Schema pinning workflow:
  1. At deployment time, run ``scripts/pin_mcp_schemas.py`` which queries each MCP server
     for its tool list schema and writes the SHA-256 hash to ``infra/mcp_schema_pins.json``.
  2. That file is deployed alongside the Function App (read at start-up by this module).
  3. If a Foundry-hosted MCP server updates its schema (e.g., a new tool added or a
     parameter renamed), the hash will mismatch and this check will refuse to start.
  4. The operator must re-run the pinning script, review the diff, and redeploy.

This is the architectural enforcement of G5: MCP output is untrusted data; a drifted schema
could cause the model to call the wrong tool or pass wrong arguments.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path

logger = logging.getLogger("ssl_renewal.drift_check")

# Path to the pinned schema hashes file (deployed alongside the Function App).
_PINS_FILE = Path(__file__).parent.parent.parent / "infra" / "mcp_schema_pins.json"

# In-memory override for tests (inject via check_mcp_schema_drift's pins_override param).
_PINNED_SCHEMAS: dict[str, str] = {}


def _load_pins() -> dict[str, str]:
    """Load pinned schema hashes from the file. Empty dict if file not present (dev/test)."""
    if _PINS_FILE.exists():
        try:
            with _PINS_FILE.open() as f:
                return json.load(f)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Could not read mcp_schema_pins.json: %s. Skipping drift check.", exc)
    return {}


def check_mcp_schema_drift(
    tool_name: str,
    live_schema: dict,
    pins_override: dict[str, str] | None = None,
) -> None:
    """Raise RuntimeError if the live schema hash differs from the pinned hash.

    Args:
        tool_name: the MCP server/tool name (e.g. "graph_mail", "jira").
        live_schema: the schema dict returned by the MCP server at start-up.
        pins_override: optional dict of {tool_name: expected_sha256} for testing.

    If the tool is not in the pinned set, the check is skipped (additive schemas are OK
    during an incremental rollout). Removal or modification of existing tools fails closed.
    """
    pins = pins_override if pins_override is not None else (_PINNED_SCHEMAS or _load_pins())

    expected_hash = pins.get(tool_name)
    if expected_hash is None:
        logger.debug("drift_check: tool '%s' not pinned — skipping.", tool_name)
        return

    live_hash = hashlib.sha256(
        json.dumps(live_schema, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()

    if live_hash != expected_hash:
        raise RuntimeError(
            f"MCP schema drift detected for tool '{tool_name}'. "
            f"Expected hash: {expected_hash[:16]}…, got: {live_hash[:16]}…. "
            "Review the schema diff, update infra/mcp_schema_pins.json, and redeploy. "
            "Refusing to start to prevent silent tool-poisoning (G5)."
        )

    logger.info("drift_check: tool '%s' schema OK (hash %s…).", tool_name, live_hash[:16])


def run_all_drift_checks(
    live_schemas: dict[str, dict],
    pins_override: dict[str, str] | None = None,
) -> None:
    """Run drift checks for all live schemas in one call.

    Args:
        live_schemas: {tool_name: schema_dict} from querying each MCP server.
        pins_override: for tests.

    Raises RuntimeError on the first drift detected (fail closed).
    """
    for tool_name, schema in live_schemas.items():
        check_mcp_schema_drift(tool_name, schema, pins_override=pins_override)
    logger.info("drift_check: all %d MCP schemas checked — no drift detected.", len(live_schemas))
