"""Tests for AuditMiddleware — G4: one structured audit line per tool call.

Verifies:
- Exactly one ``tool_call.start`` and one ``tool_call.end`` record per call (success or error).
- No private key material / secrets appear in the audit records.
- Error details are captured without exposing sensitive values.
"""
from __future__ import annotations

import json
import logging
import pytest
from typing import Any
from unittest.mock import MagicMock

from src.middleware.audit_middleware import AuditMiddleware, _summarize


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_context(tool_name: str, arguments: dict | None = None, result: Any = None) -> MagicMock:
    ctx = MagicMock()
    ctx.function = MagicMock()
    ctx.function.name = tool_name
    ctx.arguments = arguments or {}
    ctx.result = result
    return ctx


async def _ok_next(context: Any) -> None:
    context.result = {"status": "ok"}


async def _error_next(context: Any) -> None:
    raise RuntimeError("simulated_tool_error")


class CapturingHandler(logging.Handler):
    """Captures log records for assertion."""

    def __init__(self) -> None:
        super().__init__()
        self.records: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record.getMessage())

    @property
    def parsed(self) -> list[dict]:
        """Parse all captured log records as JSON."""
        return [json.loads(r) for r in self.records]


def _capture_audit_logs() -> CapturingHandler:
    handler = CapturingHandler()
    logger = logging.getLogger("ssl_renewal.audit")
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    return handler


# ---------------------------------------------------------------------------
# Basic audit records
# ---------------------------------------------------------------------------

class TestAuditMiddlewareBasic:
    @pytest.mark.asyncio
    async def test_exactly_one_start_and_one_end_on_success(self) -> None:
        """G4: exactly one start + one end record per successful tool call."""
        handler = _capture_audit_logs()
        middleware = AuditMiddleware()
        ctx = _make_context("generate_csr", {"cn": "api.example.com"})

        await middleware(ctx, _ok_next)

        records = handler.parsed
        assert len(records) == 2, f"Expected 2 records, got {len(records)}: {records}"
        assert records[0]["event"] == "tool_call.start"
        assert records[1]["event"] == "tool_call.end"
        assert records[1]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_exactly_one_start_and_one_end_on_error(self) -> None:
        """G4: error path also produces exactly one start + one end record."""
        handler = _capture_audit_logs()
        middleware = AuditMiddleware()
        ctx = _make_context("jira_create", {"ticket": "SSL-001"})

        with pytest.raises(RuntimeError):
            await middleware(ctx, _error_next)

        records = handler.parsed
        assert len(records) == 2
        assert records[0]["event"] == "tool_call.start"
        assert records[1]["event"] == "tool_call.end"
        assert records[1]["status"] == "error"
        assert records[1]["error"] == "RuntimeError"

    @pytest.mark.asyncio
    async def test_tool_name_present_in_both_records(self) -> None:
        handler = _capture_audit_logs()
        middleware = AuditMiddleware()
        ctx = _make_context("verify_cer", {"expected_cn": "api.example.com"})

        await middleware(ctx, _ok_next)

        records = handler.parsed
        assert records[0]["tool"] == "verify_cer"
        assert records[1]["tool"] == "verify_cer"


# ---------------------------------------------------------------------------
# Secrets / sensitive data must NOT appear in logs (G8)
# ---------------------------------------------------------------------------

class TestAuditSecretRedaction:
    def test_private_key_in_input_is_redacted(self) -> None:
        """Private key material must be redacted from log summaries."""
        value = {"key": "-----BEGIN RSA PRIVATE KEY-----\nABCDEFGH\n-----END RSA PRIVATE KEY-----"}
        summary = _summarize(value)
        assert "[REDACTED]" in summary
        assert "BEGIN RSA PRIVATE KEY" not in summary

    def test_bearer_token_in_input_is_redacted(self) -> None:
        value = {"authorization": "Bearer eyJhbGc..."}
        summary = _summarize(value)
        assert "[REDACTED]" in summary
        assert "Bearer " not in summary

    def test_normal_dict_is_not_redacted(self) -> None:
        value = {"cn": "api.example.com", "san": ["api.example.com"]}
        summary = _summarize(value)
        assert "[REDACTED]" not in summary
        assert "api.example.com" in summary

    def test_summary_truncated_to_limit(self) -> None:
        value = {"data": "x" * 1000}
        summary = _summarize(value, limit=256)
        assert len(summary) <= 256

    @pytest.mark.asyncio
    async def test_private_key_in_arguments_does_not_appear_in_log(self) -> None:
        """An arguments dict containing a private key must be redacted in the audit log."""
        handler = _capture_audit_logs()
        middleware = AuditMiddleware()
        ctx = _make_context("bad_tool", {
            "key_material": "-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----"
        })

        await middleware(ctx, _ok_next)

        for record_str in handler.records:
            assert "BEGIN RSA PRIVATE KEY" not in record_str

    @pytest.mark.asyncio
    async def test_csr_hash_is_safe_to_log(self) -> None:
        """SHA-256 hash of CSR (not the PEM body) should pass through unredacted."""
        handler = _capture_audit_logs()
        middleware = AuditMiddleware()
        ctx = _make_context("generate_csr", {"csr_pem_sha256": "a" * 64})

        await middleware(ctx, _ok_next)

        start_record = handler.parsed[0]
        # The SHA-256 hex string should appear (it is not a secret)
        assert "a" * 32 in str(start_record)  # truncated but present


# ---------------------------------------------------------------------------
# Error propagation
# ---------------------------------------------------------------------------

class TestAuditErrorPropagation:
    @pytest.mark.asyncio
    async def test_error_is_reraised_after_logging(self) -> None:
        """AuditMiddleware must re-raise the original exception after logging."""
        handler = _capture_audit_logs()
        middleware = AuditMiddleware()
        ctx = _make_context("graph_mail")

        with pytest.raises(RuntimeError, match="simulated_tool_error"):
            await middleware(ctx, _error_next)

        # Audit record must show the error
        end_record = handler.parsed[1]
        assert end_record["status"] == "error"
