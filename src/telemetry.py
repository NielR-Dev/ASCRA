"""OpenTelemetry setup for the SSL Certificate Renewal Agent.

Every span is keyed by workflow_id + thread_id so that App Insights
End-to-End Transaction view can reconstruct any renewal from alert to CHG.

Usage:
    # At function-app startup (host.json startup hook or __init__ of orchestrate function):
    from src.telemetry import setup_telemetry
    setup_telemetry()

    # Around every tool call (already wired into AuditMiddleware):
    from src.telemetry import tool_span
    with tool_span("generate_csr", workflow_id="wf_001", cn="api.example.com") as span:
        result = generate_csr(...)

Tracing conventions:
  - Root span per HTTP request: name = "ssl_renewal.orchestrate"
  - Child span per tool call: name = "tool.<tool_name>"
  - Child span per MCP call:  name = "mcp.<server_name>"
  - State transition events:  span.add_event("state_transition", {before, after})
  - All spans carry: workflow_id, batch_id (if set), environment
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Generator

logger = logging.getLogger("ssl_renewal.telemetry")

# OpenTelemetry imports are optional — the app must still run when the otel
# packages are not installed (dev environments without the full dependency set).
try:
    from opentelemetry import trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME
    _OTEL_AVAILABLE = True
except ImportError:  # pragma: no cover
    _OTEL_AVAILABLE = False

try:
    from azure.monitor.opentelemetry.exporter import AzureMonitorTraceExporter
    _AZURE_EXPORTER_AVAILABLE = True
except ImportError:  # pragma: no cover
    _AZURE_EXPORTER_AVAILABLE = False


_tracer: Any = None
_provider: Any = None


def setup_telemetry() -> None:
    """Initialise the global TracerProvider.

    Call once at function-app startup. Safe to call multiple times (idempotent).
    If App Insights connection string is not configured, spans are created but
    not exported (no-op exporter).
    """
    global _tracer, _provider

    if not _OTEL_AVAILABLE:
        logger.warning("opentelemetry packages not installed — tracing disabled")
        return

    # Lazy import to avoid circular import with config
    from src.config import settings

    resource = Resource.create({SERVICE_NAME: "ssl-renewal-agent"})
    _provider = TracerProvider(resource=resource)

    if _AZURE_EXPORTER_AVAILABLE and settings.applicationinsights_connection_string:
        exporter = AzureMonitorTraceExporter(
            connection_string=settings.applicationinsights_connection_string
        )
        _provider.add_span_processor(BatchSpanProcessor(exporter))
        logger.info("telemetry: Azure Monitor exporter configured")
    else:
        logger.info("telemetry: no exporter configured — spans are no-ops")

    trace.set_tracer_provider(_provider)
    _tracer = trace.get_tracer("ssl_renewal", schema_url="https://opentelemetry.io/schemas/1.11.0")
    logger.info("telemetry: TracerProvider initialised")


def _get_tracer() -> Any:
    """Return the global tracer, initialising with a no-op provider if needed."""
    if _tracer is not None:
        return _tracer
    if not _OTEL_AVAILABLE:
        return _NoOpTracer()
    return trace.get_tracer("ssl_renewal")


@contextmanager
def tool_span(
    tool_name: str,
    workflow_id: str,
    batch_id: str = "",
    **attrs: Any,
) -> Generator[Any, None, None]:
    """Context manager: creates a child span for a single tool call.

    Args:
        tool_name:    Name of the tool (e.g. "generate_csr", "verify_cer").
        workflow_id:  Workflow identifier — the primary correlation key.
        batch_id:     Batch identifier (set when running in batch mode).
        **attrs:      Additional span attributes (will be str-coerced).

    Usage:
        with tool_span("verify_cer", workflow_id=wf_id, cn=cn) as span:
            result = verify_cer(cer_b64, cn, san, wf_id)
            span.add_event("verify_result", {"pass_": str(result.pass_)})
    """
    tracer = _get_tracer()
    if not _OTEL_AVAILABLE or isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(f"tool.{tool_name}") as span:
        span.set_attribute("workflow_id", workflow_id)
        span.set_attribute("tool.name", tool_name)
        if batch_id:
            span.set_attribute("batch_id", batch_id)
        for k, v in attrs.items():
            span.set_attribute(k, str(v))
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR, str(exc))
            raise


@contextmanager
def request_span(
    operation: str,
    workflow_id: str,
    batch_id: str = "",
) -> Generator[Any, None, None]:
    """Context manager: creates a root span for an inbound request (HTTP/Service Bus).

    Usage (in orchestrate/__init__.py):
        with request_span("ssl_renewal.orchestrate", workflow_id=wf_id):
            result = await run_orchestration(payload)
    """
    tracer = _get_tracer()
    if not _OTEL_AVAILABLE or isinstance(tracer, _NoOpTracer):
        yield _NoOpSpan()
        return

    with tracer.start_as_current_span(operation) as span:
        span.set_attribute("workflow_id", workflow_id)
        span.set_attribute("span.kind", "server")
        if batch_id:
            span.set_attribute("batch_id", batch_id)
        try:
            yield span
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(trace.StatusCode.ERROR, str(exc))
            raise


def record_state_transition(
    workflow_id: str,
    state_before: str,
    state_after: str,
) -> None:
    """Add a state-transition event to the current active span.

    Call this immediately after every WorkflowState.transition() call.
    """
    if not _OTEL_AVAILABLE:
        return
    current_span = trace.get_current_span()
    if current_span and current_span.is_recording():
        current_span.add_event(
            "state_transition",
            {
                "workflow_id": workflow_id,
                "state.before": state_before,
                "state.after": state_after,
            },
        )


# ---------------------------------------------------------------------------
# No-op fallback — used when OTel packages are absent (dev, test environments)
# ---------------------------------------------------------------------------

class _NoOpSpan:
    """Drop-in span that discards all operations."""

    def set_attribute(self, key: str, value: Any) -> None:
        pass

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        pass

    def record_exception(self, exc: Exception) -> None:
        pass

    def set_status(self, *args: Any) -> None:
        pass

    def is_recording(self) -> bool:
        return False


class _NoOpTracer:
    """Drop-in tracer that returns _NoOpSpans."""

    def start_as_current_span(self, name: str) -> Any:
        from contextlib import contextmanager as _cm

        @_cm
        def _ctx() -> Generator[_NoOpSpan, None, None]:
            yield _NoOpSpan()

        return _ctx()
