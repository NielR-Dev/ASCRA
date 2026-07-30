# Phase 8c — Development: Native Tools

> **Pre-read:** [00-context.md](00-context.md) · depends on 08a, 08b, P5
> **Deliverable:** `generate_csr.py`, `verify_cer.py`, `approval_tool.py` with full test coverage
> **Task IDs:** T05, T06, T07
> **Effort estimate:** ~4 person-days

---

## Your Task

Implement the three native MAF tools. These are the most security-critical components — each one enforces one or more non-negotiable guardrails. The canonical implementations are in [07-api-tool-design.md](07-api-tool-design.md).

---

## What to Produce

1. **`src/tools/generate_csr.py`** — non-exportable HSM CSR generation (G7)
2. **`src/tools/verify_cer.py`** — deterministic X.509 verifier (G2)
3. **`src/tools/approval_tool.py`** — blocking HITL gate (G1)
4. **`tests/test_generate_csr.py`**
5. **`tests/test_verify_cer.py`**
6. **`tests/test_approval.py`**

---

## `generate_csr.py`

Use the canonical implementation from [07-api-tool-design.md](07-api-tool-design.md#generate_csr--canonical-implementation).

**Extra requirements:**
- Idempotency: if a certificate with `cert_name` already exists in Key Vault (in `PENDING` or `COMPLETED` operation state), return the existing CSR rather than creating a new one. Use the idempotency key from Cosmos (`idempotency_keys.csr_create`) to detect this.
- The returned `csr_pem` must be proper PEM with headers, 64-char line wrapping.
- Log at DEBUG level only: "Creating CSR for workflow_id={workflow_id}, cn={cn}" — nothing more (no key material).

**What NOT to do:**
- Do not log `csr_pem` contents
- Do not return the private key — it stays in Key Vault
- Do not use `exportable=True` — ever

---

## `verify_cer.py`

Use the canonical implementation from [07-api-tool-design.md](07-api-tool-design.md#verify_cer--canonical-implementation).

**Extra requirements:**
- The function is a **pure function of its inputs** — no side effects, no I/O, no network calls
- It must be fully unit-testable with fabricated PEM bytes (no Key Vault, no Cosmos)
- Use `cert.not_valid_after_utc` (not `not_valid_after`) — always timezone-aware UTC comparison
- The check dict key names (`cn_match`, `san_match`, `not_expired`, `min_validity`) are part of the public contract — do not rename them

---

## `approval_tool.py` — Full Specification

```python
# src/tools/approval_tool.py
from __future__ import annotations
from dataclasses import dataclass
import asyncio, secrets, datetime as _dt

from agent_framework import tool
from src.config import settings
from src.orchestrator.state_machine import State


@dataclass
class ApprovalPending:
    correlation_id: str    # used to validate the callback
    requested_at: str      # ISO 8601 UTC


@dataclass
class ApprovalResult:
    decision: str          # "APPROVED" or "REJECTED"
    approver: str          # Entra email of approver
    reasoning: str
    decided_at: str        # ISO 8601 UTC


# In-memory pending approvals map: correlation_id → asyncio.Future
# In production, this is replaced with a durable store (Cosmos + Service Bus)
_pending: dict[str, asyncio.Future] = {}


@tool
async def request_approval(workflow_id: str, cn: str, san: list[str],
                            owning_application: str, jira_ticket: str) -> ApprovalPending:
    """Send the Adaptive Card to PD and block the workflow until approval (HITL, G1).

    In the real system this calls the Copilot Studio / Power Automate webhook and
    then awaits the record_approval_decision callback. The workflow is suspended here.
    """
    correlation_id = secrets.token_urlsafe(16)
    loop = asyncio.get_event_loop()
    fut: asyncio.Future = loop.create_future()
    _pending[correlation_id] = fut

    # TODO: call the real Copilot Studio / Power Automate API to send the card
    # For now, register the future and return — the callback will resolve it.
    pending = ApprovalPending(
        correlation_id=correlation_id,
        requested_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    # Block until the callback resolves the future (or timeout escalates)
    try:
        result: ApprovalResult = await asyncio.wait_for(
            fut, timeout=settings.approval_timeout_hours * 3600
        )
        return result
    except asyncio.TimeoutError:
        # Auto-escalate to delegate — emit escalation audit event and raise
        raise RuntimeError(
            f"Approval timeout after {settings.approval_timeout_hours}h for {workflow_id}; "
            "escalating to PD delegate."
        )
    finally:
        _pending.pop(correlation_id, None)


@tool
async def record_approval_decision(workflow_id: str, decision: str, approver: str,
                                    reasoning: str, correlation_id: str) -> ApprovalResult:
    """Callback: called when PD taps Approve/Reject on the Adaptive Card.

    Validates: approver identity (Entra token verified at HTTP layer), correlation_id binding.
    Resolves the pending future so request_approval unblocks.
    """
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError(f"Invalid decision: {decision!r}. Must be APPROVED or REJECTED.")

    fut = _pending.get(correlation_id)
    if fut is None:
        raise ValueError(
            f"No pending approval found for correlation_id={correlation_id!r}. "
            "Possible replay or expired request."
        )

    result = ApprovalResult(
        decision=decision,
        approver=approver,
        reasoning=reasoning,
        decided_at=_dt.datetime.now(_dt.timezone.utc).isoformat(),
    )
    fut.set_result(result)
    return result
```

---

## Test Requirements

### `tests/test_generate_csr.py`

- `test_key_is_non_exportable` — assert `CertificatePolicy.exportable=False` in the KV call
- `test_key_type_is_rsa_hsm` — assert `KeyType.rsa_hsm`
- `test_wildcard_cn_rejected` — `*.example.com` raises `ToolValidationError` before any KV call
- `test_wildcard_san_rejected` — wildcard in SAN raises before any KV call
- `test_idempotent_on_workflow_id` — second call with same workflow_id returns existing result
- `test_no_private_key_in_result` — `CsrResult` fields contain no private key material

### `tests/test_verify_cer.py`

Build test helpers that generate real X.509 certificates programmatically using the `cryptography` library (do not use fixture files — generate them in code):

- `test_pass_on_exact_match` — valid cert with matching CN, SANs, 365+ days remaining → `pass_=True`
- `test_fail_on_cn_mismatch` — cert with different CN → `pass_=False`, `checks["cn_match"]=False`
- `test_fail_on_san_mismatch` — cert with extra/missing SAN → `pass_=False`
- `test_fail_on_expired` — cert with `not_after` in the past → `pass_=False`
- `test_fail_on_short_validity` — cert valid but < 365 days remaining → `pass_=False`
- `test_fail_on_bad_parse` — random bytes → `pass_=False` (should catch exception)
- `test_accepts_der_format` — DER-encoded cert → same result as PEM

### `tests/test_approval.py`

- `test_approval_resolves_on_approve` — `request_approval` returns `ApprovalResult` when callback calls `record_approval_decision` with `APPROVED`
- `test_approval_blocks_until_callback` — future is not resolved until callback fires
- `test_invalid_decision_raises` — `record_approval_decision` with `decision="MAYBE"` raises `ValueError`
- `test_unknown_correlation_id_raises` — callback with unknown `correlation_id` raises `ValueError`

---

## Acceptance Criteria

- `generate_csr` never returns private key material; Key Vault policy enforces `exportable=False`
- `verify_cer` is a pure function; fails on every mismatch type; model cannot bypass the verdict
- `request_approval` blocks the calling coroutine until the callback resolves it; timeout escalates
- All tests pass; coverage ≥ 80% for all three tool files

---

## Verification

```bash
pytest tests/test_generate_csr.py tests/test_verify_cer.py tests/test_approval.py -v --cov=src/tools
```
