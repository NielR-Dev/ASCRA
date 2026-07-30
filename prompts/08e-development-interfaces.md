# Phase 8e — Development: Interface Adapters & Function Hosts

> **Pre-read:** [00-context.md](00-context.md) · depends on 08a–08d, P7
> **Deliverable:** `interfaces/` adapters, `functions/` HTTP hosts
> **Task IDs:** T12, T20
> **Effort estimate:** ~5 person-days

---

## Your Task

Implement the three interaction-mode adapters (Direct: Slack + web console; Embedded: read model + suggestion service; Backend: event trigger + callbacks + scheduled scan) and the four Azure Function HTTP host modules.

---

## What to Produce

1. **`src/interfaces/direct/slack_adapter.py`** — Slack slash commands + signature verification
2. **`src/interfaces/direct/web_console_api.py`** — REST endpoints for web console
3. **`src/interfaces/embedded/read_model.py`** — read-only projection of workflow/batch state
4. **`src/interfaces/embedded/suggestion_service.py`** — proactive suggestions (no mutations)
5. **`src/interfaces/backend/event_trigger.py`** — Event Grid/Service Bus webhook handler
6. **`src/interfaces/backend/callbacks.py`** — approval + PKI reply callbacks
7. **`src/interfaces/backend/scheduled_scan.py`** — timer-triggered cert inventory scan
8. **`src/functions/orchestrate/__init__.py`** — Function host for `/api/orchestrate`
9. **`src/functions/approval_callback/__init__.py`** — Function host for `/api/approval-callback`
10. **`src/functions/pki_reply/__init__.py`** — Function host for `/api/pki-reply`
11. **`src/functions/status/__init__.py`** — Function host for `/api/status`
12. **`tests/test_interfaces.py`** — adapter contract tests + security tests

---

## The Critical Rule: Adapters Hold No Business Logic

Every adapter must do **only**:
1. Protocol translation (parse the incoming HTTP/event/Slack payload)
2. Authentication verification (validate the signature, token, or identity)
3. Input normalization (produce a clean dict the guarded core accepts)
4. Call the **same** guarded core entrypoint every other mode uses
5. Format the response

**Adapters must never:**
- Validate business rules
- Access Cosmos directly (except through the read model for Embedded)
- Call Key Vault
- Bypass the HITL gate
- Skip middleware

---

## `slack_adapter.py` — Key Requirements

```python
# src/interfaces/direct/slack_adapter.py
"""Slack adapter: /ssl-status, /ssl-renew, /ssl-batch commands → guarded core.

Security: verify Slack request signature on every request. Reject unsigned/replayed requests.
Auth: Slack OAuth → mapped to Entra identity for audit logging.
"""

def verify_slack_signature(request_body: bytes, timestamp: str, signature: str) -> None:
    """Raise ValueError if the Slack request signature is invalid or replayed (> 5 min old)."""
    # Use HMAC-SHA256 with SLACK_SIGNING_SECRET from settings
    # Reject if |now - timestamp| > 300 seconds (replay attack prevention)
    ...

async def handle_ssl_status(cn_or_batch_id: str, actor: str) -> dict:
    """Route to get_status on the guarded core; return current state + timeline."""
    ...

async def handle_ssl_renew(cn: str, san: list[str], actor: str) -> dict:
    """Route to run_child on the guarded core. Will block on PD approval (G1) before continuing."""
    ...

async def handle_ssl_batch(wave_alerts: list[dict], actor: str) -> dict:
    """Route to run_batch on the guarded core."""
    ...
```

---

## `web_console_api.py` — Key Requirements

Expose these endpoints (use FastAPI or Azure Functions, whichever fits the project's approach):

| Endpoint | Method | Auth | Returns |
|----------|--------|------|---------|
| `/api/v1/workflows/{workflow_id}` | GET | Entra SSO | State + timeline + links |
| `/api/v1/batches/{batch_id}` | GET | Entra SSO | Batch record + child summary |
| `/api/v1/approvals` | GET | Entra SSO, approver role | Pending approvals queue |

All endpoints enforce role-scoped access (viewer vs requester vs approver).

---

## `read_model.py` — Embedded (Read Only)

```python
# src/interfaces/embedded/read_model.py
"""Read-only projection of workflow and batch state for dashboards and embedded suggestions.

This is the ONLY Cosmos access permitted in the Embedded adapter.
No mutations. No tool calls. No Key Vault access.
"""

async def get_workflow_summary(workflow_id: str) -> dict:
    """Return {state, cn, updated_at, cer_blob_url} — no audit details."""
    ...

async def get_expiring_soon(days_ahead: int = 30) -> list[dict]:
    """Return certs in workflow_state not yet in a terminal state and expiring within days_ahead."""
    ...
```

---

## `suggestion_service.py` — Embedded (Read Only)

```python
# src/interfaces/embedded/suggestion_service.py
"""Generate proactive suggestions for the dashboard. No mutations, no tool calls.

A suggestion is a hint + an action_ref that the user must explicitly accept.
Accepting a suggestion emits a normal Direct or Backend request — never a direct mutation.
"""

async def get_suggestions() -> list[dict]:
    """Return [{kind, cn, rationale, action_ref}] based on current cert inventory state.
    
    Example: {"kind": "expiry_wave", "cn": null, "rationale": "12 certs expire in 30 days",
              "action_ref": "/api/v1/batch"}
    """
    ...
```

---

## `event_trigger.py` — Backend (Event Grid / Service Bus)

```python
# src/interfaces/backend/event_trigger.py
"""Backend adapter: Dynatrace webhook → Event Grid → Service Bus → Orchestrator.

Validates: Event Grid subscription validation handshake; signed webhook payload.
Normalizes: extracts alert dict and routes to run_batch or run_child on the guarded core.
"""

async def handle_event_grid_event(event: dict) -> dict:
    """Handle one Event Grid event (Dynatrace SSL-expiry alert).
    Validates the event schema and routes to the orchestrator.
    """
    ...
```

---

## Azure Function: `orchestrate/__init__.py` — Canonical Implementation

```python
# src/functions/orchestrate/__init__.py
"""HTTP-triggered Function: entrypoint called by Logic App after dequeuing an alert."""
from __future__ import annotations
import json
import logging
import azure.functions as func
from src.config import settings
from src.orchestrator.agent import build_orchestrator

logger = logging.getLogger("ssl_renewal.orchestrate")


async def main(req: func.HttpRequest) -> func.HttpResponse:
    # Kill-switch check (G8)
    if not settings.agent_enabled:
        return func.HttpResponse(
            json.dumps({"error": {"code": "agent_disabled", "message": "Agent is currently disabled."}}),
            status_code=503, mimetype="application/json",
        )

    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": {"code": "bad_request", "message": "Invalid JSON body."}}),
            status_code=400, mimetype="application/json",
        )

    alert = body.get("alert")
    if not alert:
        return func.HttpResponse(
            json.dumps({"error": {"code": "missing_alert", "message": "'alert' field is required."}}),
            status_code=400, mimetype="application/json",
        )

    correlation_id = req.headers.get("x-correlation-id", "")
    agent = build_orchestrator()
    result = await agent.run(f"New SSL expiry alert: {json.dumps(alert)}")
    return func.HttpResponse(
        json.dumps({"state": "PARSED", "message": result.text, "correlation_id": correlation_id}),
        status_code=200, mimetype="application/json",
    )
```

---

## Contract Test Requirements

### `tests/test_interfaces.py`

These tests are **mandatory** (referenced in P9 security tests):

- `test_all_modes_hit_guarded_core` — Direct (Slack `/ssl-renew`), accepted Embedded suggestion, and Backend (event/API) all ultimately call the same `run_child`/`run_batch` entrypoint, which blocks on PD approval (G1) and runs the verifier (G2)

- `test_embedded_is_read_only` — Embedded adapter methods (`read_model`, `suggestion_service`) call no tool that mutates state and make no calls to `run_child`, `run_batch`, `generate_csr`, or any approval tool

- `test_slack_signature_required` — request without a valid Slack signature → `ValueError`; replayed request (timestamp > 5 min old) → `ValueError`

- `test_adapter_has_no_logic` — contract test: no business rules in adapter code; each adapter module only calls public core entrypoints (`run_child`, `run_batch`, `get_status`)

- `test_orchestrate_function_returns_503_when_disabled` — `AGENT_ENABLED=false` → 503

- `test_orchestrate_function_requires_alert_field` — missing `alert` → 400

---

## Acceptance Criteria

- All three modes (Direct/Slack, Embedded, Backend/event) reach the one guarded core
- Slack adapter rejects unsigned and replayed requests
- Embedded adapter makes no mutations — read + suggest only
- Function hosts check the kill-switch before processing
- All four Function endpoints return structured error envelopes and echo `correlation_id`
- Contract tests green

---

## Verification

```bash
pytest tests/test_interfaces.py tests/test_api.py -v
```
