"""Typed error taxonomy for all native tools.

These three exception classes define the error taxonomy that every @tool must raise
when something goes wrong. PolicyMiddleware's G3 counter is triggered by any exception
that propagates up from a tool call.
"""
from __future__ import annotations


class ToolValidationError(ValueError):
    """Bad arguments — non-retryable (400-equivalent).

    Raised when tool input fails validation: wildcard CN/SAN, missing required arg,
    invalid format. The orchestrator should not retry a ToolValidationError.
    """


class ToolTransientError(RuntimeError):
    """Transient failure — bounded retry with backoff (429/503-equivalent).

    Raised for temporary external failures: APIM throttle, downstream 503, network timeout.
    The orchestrator may retry within the configured bounds before escalating.
    """


class ToolFatalError(RuntimeError):
    """Unexpected fatal failure — halt + escalate (500-equivalent).

    Raised for unrecoverable errors: Key Vault unavailable after retries, internal
    state inconsistency, authentication failure. Triggers G3 halt after max_consecutive_errors.
    """
