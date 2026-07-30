"""Tests for PolicyMiddleware — G1, G2, G3, G6 guardrails.

Tests are fully synchronous-friendly via pytest-asyncio.
PolicyMiddleware is tested in isolation via a fake context + fake next_handler.
"""
from __future__ import annotations

import pytest
import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock

from src.middleware.policy_middleware import PolicyMiddleware, PolicyViolation, _is_wildcard


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(tool_name: str, arguments: dict | None = None) -> MagicMock:
    """Build a minimal fake MAF tool context."""
    ctx = MagicMock()
    ctx.function = MagicMock()
    ctx.function.name = tool_name
    ctx.arguments = arguments or {}
    return ctx


async def _ok_next(context: Any) -> None:
    """A next_handler that succeeds silently."""


async def _error_next(context: Any) -> None:
    """A next_handler that raises a generic ToolTransientError."""
    raise RuntimeError("Simulated transient tool error")


# ---------------------------------------------------------------------------
# _is_wildcard helper
# ---------------------------------------------------------------------------

class TestIsWildcard:
    def test_wildcard_star_dot(self) -> None:
        assert _is_wildcard("*.example.com") is True

    def test_wildcard_bare_star(self) -> None:
        assert _is_wildcard("*") is True

    def test_wildcard_with_spaces(self) -> None:
        assert _is_wildcard("  *.example.com  ") is True

    def test_normal_hostname(self) -> None:
        assert _is_wildcard("api.prod.example.com") is False

    def test_empty_string(self) -> None:
        assert _is_wildcard("") is False


# ---------------------------------------------------------------------------
# G6 — wildcard blocking
# ---------------------------------------------------------------------------

class TestWildcardBlocking:
    @pytest.mark.asyncio
    async def test_wildcard_cn_raises_before_side_effect(self) -> None:
        """G6: *.example.com CN must be blocked before generate_csr reaches Key Vault."""
        policy = PolicyMiddleware()
        ctx = _make_context("generate_csr", {"cn": "*.example.com", "san": ["api.example.com"]})

        next_called = False

        async def _spy_next(_ctx: Any) -> None:
            nonlocal next_called
            next_called = True

        with pytest.raises(PolicyViolation, match="Wildcard"):
            await policy(ctx, _spy_next)

        assert next_called is False, "next_handler must NOT be called on wildcard (G6)"

    @pytest.mark.asyncio
    async def test_wildcard_san_raises(self) -> None:
        """G6: wildcard in SAN list also blocked."""
        policy = PolicyMiddleware()
        ctx = _make_context("generate_csr", {"cn": "api.example.com", "san": ["*.example.com"]})
        with pytest.raises(PolicyViolation, match="Wildcard"):
            await policy(ctx, _ok_next)

    @pytest.mark.asyncio
    async def test_bare_star_raises(self) -> None:
        """G6: bare * in cn also blocked."""
        policy = PolicyMiddleware()
        ctx = _make_context("generate_csr", {"cn": "*", "san": []})
        with pytest.raises(PolicyViolation):
            await policy(ctx, _ok_next)

    @pytest.mark.asyncio
    async def test_valid_cn_passes_through(self) -> None:
        """Non-wildcard CN passes the policy check."""
        policy = PolicyMiddleware()
        ctx = _make_context("generate_csr", {
            "cn": "api.prod.example.com",
            "san": ["api.prod.example.com", "api-internal.prod.example.com"],
        })
        # Should not raise
        await policy(ctx, _ok_next)

    @pytest.mark.asyncio
    async def test_non_generate_csr_tool_not_checked_for_wildcard(self) -> None:
        """Wildcard check only applies to generate_csr, not other tools."""
        policy = PolicyMiddleware()
        ctx = _make_context("graph_mail", {"to": "*.example.com"})
        # Should not raise for a different tool
        await policy(ctx, _ok_next)


# ---------------------------------------------------------------------------
# G3 — consecutive error halting
# ---------------------------------------------------------------------------

class TestConsecutiveErrorHalting:
    @pytest.mark.asyncio
    async def test_first_error_does_not_halt(self) -> None:
        """First error is raised but does not trigger a PolicyViolation halt."""
        policy = PolicyMiddleware()
        ctx = _make_context("verify_cer")

        with pytest.raises(RuntimeError, match="Simulated transient tool error"):
            await policy(ctx, _error_next)

        assert policy.consecutive_errors == 1
        # Not a PolicyViolation
        try:
            await policy(ctx, _error_next)
        except PolicyViolation:
            pass  # Expected on 2nd consecutive error
        except RuntimeError:
            pass  # Would also be acceptable if threshold not reached yet

    @pytest.mark.asyncio
    async def test_consecutive_errors_halt_at_threshold(self) -> None:
        """G3: after max_consecutive_tool_errors (default 2) consecutive failures, halt."""
        policy = PolicyMiddleware()
        ctx = _make_context("graph_mail")

        # First error — should raise RuntimeError (not halted yet)
        with pytest.raises(RuntimeError, match="Simulated"):
            await policy(ctx, _error_next)

        assert policy.consecutive_errors == 1

        # Second error — should raise PolicyViolation (halt threshold reached)
        with pytest.raises(PolicyViolation, match="Halting"):
            await policy(ctx, _error_next)

        assert policy.consecutive_errors == 2

    @pytest.mark.asyncio
    async def test_success_resets_error_counter(self) -> None:
        """A successful call after an error resets the consecutive counter."""
        policy = PolicyMiddleware()
        ctx = _make_context("jira_create")

        # One error
        with pytest.raises(RuntimeError):
            await policy(ctx, _error_next)
        assert policy.consecutive_errors == 1

        # Then a success
        await policy(ctx, _ok_next)
        assert policy.consecutive_errors == 0

        # Another error — counter restarts from 0, so first error doesn't halt
        with pytest.raises(RuntimeError):
            await policy(ctx, _error_next)
        assert policy.consecutive_errors == 1

    @pytest.mark.asyncio
    async def test_policy_violation_not_counted_as_tool_error(self) -> None:
        """A PolicyViolation (e.g. from wildcard check) is not counted toward G3 threshold."""
        policy = PolicyMiddleware()
        wildcard_ctx = _make_context("generate_csr", {"cn": "*.example.com", "san": []})

        with pytest.raises(PolicyViolation):
            await policy(wildcard_ctx, _ok_next)

        # consecutive_errors should NOT be incremented for a PolicyViolation
        assert policy.consecutive_errors == 0

    def test_reset_method(self) -> None:
        """reset() clears the consecutive error counter."""
        policy = PolicyMiddleware()
        policy._consecutive_errors = 3
        policy.reset()
        assert policy.consecutive_errors == 0
