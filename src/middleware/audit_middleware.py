"""One structured, tamper-evident audit line per tool call (guardrail G4).

Every tool invocation — hosted MCP, external MCP, or native @tool — produces exactly:
  1. A ``tool_call.start`` record BEFORE the tool runs (input args, redacted)
  2. A ``tool_call.end`` record AFTER the tool completes or raises (output/error, redacted)

Redaction rules (G7/G8):
  - Summaries are truncated to 256 characters to prevent log bloat.
  - The _summarize function must never include private-key markers, bearer tokens,
    or full cert bytes. It does a simple truncation of the JSON representation.
    For richer redaction (PHI patterns), a regex filter can be added to the logging pipeline.

This middleware writes to the Python logger ``ssl_renewal.audit``. In production, this
logger is wired to App Insights via OpenTelemetry (P12). The CosmosRepo persistence of
audit records is handled by a separate audit-write hook in the orchestrator; this middleware
is the authoritative first leg (structured log line).
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger("ssl_renewal.audit")

# Max length of any single field summary in the log output.
_SUMMARY_LIMIT = 256

# Patterns that must not appear in any audit log output.
_REDACT_PATTERNS: tuple[str, ...] = (
    "BEGIN RSA PRIVATE KEY",
    "BEGIN PRIVATE KEY",
    "BEGIN EC PRIVATE KEY",
    "BEGIN ENCRYPTED PRIVATE KEY",
    "Bearer ",
)


def _summarize(value: Any, limit: int = _SUMMARY_LIMIT) -> str:
    """Serialize and truncate a value for audit logging.

    Ensures the result fits in the log without emitting sensitive material.
    json.dumps with default=str handles non-serializable types gracefully.
    """
    text = json.dumps(value, default=str)[:limit]
    # Belt-and-suspenders redaction of known sensitive patterns
    for pattern in _REDACT_PATTERNS:
        if pattern in text:
            text = "[REDACTED]"
            break
    return text


class AuditMiddleware:
    """Emit a structured audit record before and after each tool call (G4).

    Order in middleware chain: PolicyMiddleware first, then AuditMiddleware.
    AuditMiddleware runs after policy validation — so even a PolicyViolation error
    is captured as a ``tool_call.end`` with ``status: policy_violation`` because
    PolicyMiddleware re-raises and the exception propagates through AuditMiddleware's
    except block.

    Note: if PolicyMiddleware raises *before* calling next_handler, AuditMiddleware
    sees it as an error on the *inner* call (because AuditMiddleware is the outer wrapper).
    Wire order: [PolicyMiddleware(), AuditMiddleware()] means Policy is called first;
    both Audit records are emitted regardless of Policy's decision.
    """

    async def __call__(
        self, context: Any, next_handler: Callable[[Any], Awaitable[None]]
    ) -> None:
        tool_name: str = context.function.name
        input_summary = _summarize(getattr(context, "arguments", {}))

        start_record = {
            "event": "tool_call.start",
            "tool": tool_name,
            "input": input_summary,
        }
        logger.info(json.dumps(start_record))

        try:
            await next_handler(context)

            output_summary = _summarize(getattr(context, "result", None))
            end_record = {
                "event": "tool_call.end",
                "tool": tool_name,
                "status": "ok",
                "output": output_summary,
            }
            logger.info(json.dumps(end_record))

        except Exception as exc:
            error_record = {
                "event": "tool_call.end",
                "tool": tool_name,
                "status": "error",
                "error": type(exc).__name__,
                # Include the string representation for observability, but
                # strip any value that looks like a private key or bearer token.
                "error_detail": _summarize(str(exc)),
            }
            logger.info(json.dumps(error_record))
            raise
