"""Hard guardrails enforced BEFORE any tool executes (G1, G2, G3, G6).

The LLM cannot bypass these: middleware runs deterministically around every tool call.
These are architectural security controls — not prompts — and they fail closed.

Guardrails enforced here:
  G3 — halt + escalate after max_consecutive_tool_errors consecutive failures
  G6 — block wildcard CN/SAN before generate_csr reaches Key Vault

Guardrails G1 and G2 are enforced by the state machine and native tools (request_approval,
verify_cer) — PolicyMiddleware is the first line; the state machine is the second.
"""
from __future__ import annotations

from typing import Any, Awaitable, Callable

from src.config import settings


class PolicyViolation(RuntimeError):
    """Raised when a tool call violates a non-negotiable guardrail.

    This is a non-retryable error. PolicyMiddleware re-raises it after the G3 counter check.
    """


def _is_wildcard(value: str) -> bool:
    """Return True if the value is a wildcard hostname pattern (G6)."""
    stripped = value.strip()
    return stripped.startswith("*.") or stripped == "*"


class PolicyMiddleware:
    """MAF function middleware: validate args, block wildcards, bound consecutive errors.

    Order in middleware chain: PolicyMiddleware FIRST, then AuditMiddleware.
    This ensures a guardrail violation is logged by Audit (it re-raises the exception)
    and is visible in the audit trail, but the violation is caught here before the tool runs.

    Thread-safety: each orchestrator instance owns its own PolicyMiddleware instance.
    The _consecutive_errors counter is per-instance (per-workflow), not global.
    """

    def __init__(self) -> None:
        self._consecutive_errors: int = 0

    @property
    def consecutive_errors(self) -> int:
        """Expose for testing."""
        return self._consecutive_errors

    def reset(self) -> None:
        """Reset the error counter (called by tests or on workflow resumption)."""
        self._consecutive_errors = 0

    async def __call__(
        self, context: Any, next_handler: Callable[[Any], Awaitable[None]]
    ) -> None:
        """Intercept every tool call. Validate args; then call next; track consecutive errors.

        The parameter is named ``next_handler`` to avoid shadowing the built-in ``next``.
        MAF 1.0 passes the next middleware/tool in the chain.
        """
        args: dict[str, Any] = getattr(context, "arguments", {}) or {}
        tool_name: str = context.function.name

        # G6 — never generate a wildcard CSR.
        # Check before any Key Vault call so no HSM key is even attempted.
        if tool_name == "generate_csr":
            cn = str(args.get("cn", ""))
            san = [str(s) for s in args.get("san", [])]
            if _is_wildcard(cn) or any(_is_wildcard(s) for s in san):
                raise PolicyViolation(
                    "Wildcard certificates are not permitted by policy (G6). "
                    "Route this request to the CAB for separate approval."
                )

        # G3 — halt + escalate after N consecutive tool errors.
        try:
            await next_handler(context)
            # Success: reset the consecutive error counter.
            self._consecutive_errors = 0

        except PolicyViolation:
            # Policy violations are always hard stops; don't count them as transient errors.
            raise

        except Exception:
            self._consecutive_errors += 1
            if self._consecutive_errors >= settings.max_consecutive_tool_errors:
                raise PolicyViolation(
                    f"Halting workflow: {self._consecutive_errors} consecutive tool errors "
                    f"(threshold: {settings.max_consecutive_tool_errors}). "
                    "Escalate to Product Director / on-call. Do not retry autonomously."
                )
            # Below the threshold: re-raise the original error so the orchestrator can retry.
            raise
