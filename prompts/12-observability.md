# Phase 12 — Observability

> **Pre-read:** [00-context.md](00-context.md) · depends on P8, P11
> **Deliverable:** OTel instrumentation, dashboards, alert rules, Purview lineage
> **Task IDs:** T17
> **Effort estimate:** ~4 person-days

---

## Your Task

Instrument the system with OpenTelemetry, build the operational dashboards, configure alert rules, and enable Purview lineage. Every renewal must be fully traceable from alert to CHG by `workflow_id`.

---

## What to Produce

1. **`src/telemetry.py`** — OTel setup, span helpers, correlation propagation
2. **`infra/appinsights.bicep`** — App Insights workspace + Log Analytics + alert rules
3. **`dashboards/renewal-funnel.workbook.json`** — Azure Workbook: Renewal Funnel
4. **`dashboards/power-bi-approvals.pbix`** (or equivalent template) — Power BI: PD Approvals
5. **`docs/runbook-alerts.md`** — alert runbook (what to do when each alert fires)

---

## Signals & KPIs (instrument all four layers)

| Layer | What to track |
|-------|--------------|
| **Business** | Renewals started / completed; mean cycle time; % autonomous; approval SLA breaches; rejection rate |
| **Agent** | Tool-call count/latency/error rate per tool; retry rounds; escalations; groundedness/eval scores |
| **MCP** | Per-server latency/error rate/throttle rate; schema-drift alerts |
| **Ops** | Function invocations/failures/cold starts; Cosmos RU consumption + throttles; Service Bus queue depth/age; Content-Safety block rate |

---

## OTel Instrumentation (`src/telemetry.py`)

```python
# src/telemetry.py
"""OpenTelemetry setup. Every span is keyed by workflow_id + thread_id."""
from __future__ import annotations
from contextlib import contextmanager
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from src.config import settings

_tracer = None


def setup_telemetry() -> None:
    """Call once at function-app startup."""
    global _tracer
    provider = TracerProvider()
    if settings.appinsights_connection_string:
        exporter = OTLPSpanExporter(endpoint="https://dc.applicationinsights.azure.com/v2.1/track")
        provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("ssl_renewal")


@contextmanager
def tool_span(tool_name: str, workflow_id: str, **attrs):
    """Context manager: creates a child span for a tool call."""
    tracer = _tracer or trace.get_tracer("ssl_renewal")
    with tracer.start_as_current_span(f"tool.{tool_name}") as span:
        span.set_attribute("workflow_id", workflow_id)
        span.set_attribute("tool.name", tool_name)
        for k, v in attrs.items():
            span.set_attribute(k, str(v))
        yield span
```

Add `tool_span` calls inside `AuditMiddleware` so every tool call produces a child span under the request's root span.

---

## Tracing Convention

- Root span per HTTP request: `ssl_renewal.orchestrate`, keyed by `workflow_id`
- Child spans: one per tool call (`tool.generate_csr`, `tool.verify_cer`, etc.)
- Child spans for each MCP call: `mcp.jira`, `mcp.graph_mail`, etc.
- State transition events: add as span events (`state_transition: CSR_REQUESTED → APPROVED`)
- All spans carry `workflow_id` + `batch_id` (when applicable) as span attributes

App Insights End-to-End Transaction view must be able to reconstruct any renewal by searching `workflow_id`.

---

## Alert Rules (configure in `infra/appinsights.bicep`)

| Alert | Condition | Severity | Route to |
|-------|-----------|----------|---------|
| Stuck workflow | `workflow_state.updated_at` > 24h ago AND state not terminal | Sev2 | SRE + Teams |
| Verifier failure | `tool.verify_cer.pass_=false` count > 0 in 1h window | Sev2 | SRE + Teams |
| Content-Safety block rate | `content_safety_blocked` > 1% of requests | Sev1 | Security team |
| Consecutive-tool-error halt | `policy_violation.consecutive_errors` event fired | Sev1 | SRE + PD |
| Cosmos throttling | `cosmos_throttled_requests` > 0 sustained 5min | Sev3 | SRE |
| Service Bus dead-letter | `dead_letter_message_count` > 0 | Sev2 | SRE |
| PKI reply overdue | No PKI reply after 5 business days | Sev2 | SRE + PD |
| Schema drift detected | `mcp_schema_drift` event fired | Sev1 | Security + SRE |

---

## Renewal Funnel Dashboard (Azure Workbook)

Build a funnel visualization with drop-off counts at each state:

```
ALERT_RECEIVED → PARSED → CSR_REQUESTED → APPROVED → PKI_REPLIED → VERIFIED → COMPLETE
      │               │           │            │            │           │
    [N]             [N-n1]      [N-n2]       [N-n3]      [N-n4]      [N-n5]
                                            ↓REJECTED   ↓FAILED
```

Include panels for:
- Mean cycle time (last 7d)
- Stuck workflows (not updated in > 24h)
- Retry/escalation rate
- Batch throughput (renewals/hour)

---

## Power BI Approvals Dashboard

Track:
- PD decision times (time from card sent to decision)
- Rejection reasons (free-text analysis)
- Approval SLA breach rate (> 48h before auto-escalation)
- Per-PD delegate breakdown

---

## Purview Lineage

Enable Purview data lineage to trace:
- CER artifact → Blob URL → `workflow_state.verification.cer_blob_url`
- `audit_log` documents → Purview classification (no PHI, confirm)
- Key Vault key reference → `workflow_state.csr.key_vault_key_id`

---

## Acceptance Criteria

- Any `workflow_id` is fully traceable in App Insights End-to-End Transaction view
- All 8 alert rules are deployed and tested (see verification)
- KPI dashboards populate from live data after deployment
- OTel spans carry `workflow_id` on every tool call

---

## Verification

```bash
# Trigger a synthetic stuck workflow → verify the >24h alert fires
# (in UAT: create a workflow, don't progress it, wait or mock the time check)

# Trigger a forced verifier failure → verify the verifier-failure alert fires
pytest tests/test_observability.py -v
```

- Trace search in App Insights returns the full span tree for a known `workflow_id`
- Dashboard loads and shows non-zero renewal funnel counts after a test run
- Alert test: `az monitor metrics alert test --rule stuck-workflow` fires as expected
