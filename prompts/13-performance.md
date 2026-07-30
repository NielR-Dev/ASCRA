# Phase 13 — Performance

> **Pre-read:** [00-context.md](00-context.md) · depends on P8d (batch), P11
> **Deliverable:** Idempotency store wired, SLOs verified, load test report, timer tests
> **Task IDs:** T13, T19
> **Effort estimate:** ~3 person-days

---

## Your Task

Verify the system meets its performance SLOs under load: no duplicate side effects, bounded concurrency works, rate limiters prevent quota breaches, and the expiry-wave drains within 1 business day. Implement the reminder/escalation timers.

---

## What to Produce

1. **`src/persistence/idempotency.py`** (or integrated in `cosmos_repo.py`) — idempotency key check+record
2. **`src/orchestrator/timers.py`** — approval auto-escalation (48h) + PKI reminders (24h/72h)
3. **`tests/test_performance.py`** — load test + SLO assertions
4. **`tests/test_timers.py`** — timer behavior tests with injected clock
5. **`docs/performance-report.md`** — load test results (to be filled post-run)

---

## SLOs (every release must verify these)

| Metric | SLO | How to verify |
|--------|-----|---------------|
| Autonomous step latency (p95, unblocked) | < 60s | Measure in E2E synthetic test |
| Alert → `CSR_REQUESTED` (p95) | < 5 min | Trace timestamps |
| Approval unblock → PKI email sent | < 2 min | Trace timestamps |
| CER received → verified verdict | < 60s | Trace timestamps |
| Duplicate external side effects | **0** | Load test assertion |
| Run-plane trigger availability | ≥ 99.9% | Azure SLA + zone redundancy |
| Batch throughput (sustained) | **≥ 100 renewals/hour** | Load test |
| Concurrent children in flight | **10–100+** (bounded) | Load test concurrency assertion |
| Expiry-wave (100 certs) time-to-all-submitted | **< 1 business day** | Load test |
| Per-lane downstream quota breaches | **0** | Rate limiter assertions |
| Sibling isolation (one child failure aborts others) | **Never** | FR-15 batch test |

---

## Idempotency Implementation

Every external side-effect must pass an idempotency key stored in Cosmos before execution:

```python
# src/persistence/idempotency.py

async def check_or_record(cosmos_repo, idempotency_key: str, result: dict | None = None) -> dict | None:
    """
    If idempotency_key exists in Cosmos: return the stored result (replay).
    If it doesn't exist and result is provided: store it and return None (first execution).
    If it doesn't exist and result is None: return None (first execution, no result yet).
    
    TTL: 30 days (configured on the idempotency container).
    """
    ...
```

Use this before every:
- `jira.create_issue()` call → key: `jira_create_{workflow_id}`
- `graph_mail.send()` call → key: `email_send_{workflow_id}`
- `servicenow.create_change()` call → key: `chg_create_{workflow_id}`

On retry (e.g. transient error after the Jira ticket was created but before the result was saved), the second call finds the key and returns the original ticket ID — no duplicate ticket.

---

## Timers (`src/orchestrator/timers.py`)

```python
# src/orchestrator/timers.py
"""Timers for approval auto-escalation and PKI reminder emails.

All timers use injected clock (now_fn) for testability — never call datetime.now() directly.
"""
from __future__ import annotations
import datetime as _dt
from typing import Callable

from src.config import settings


def should_escalate_approval(
    requested_at: _dt.datetime,
    now_fn: Callable[[], _dt.datetime] | None = None,
) -> bool:
    """True if APPROVAL_TIMEOUT_HOURS has elapsed without a decision."""
    now = (now_fn or (lambda: _dt.datetime.now(_dt.timezone.utc)))()
    elapsed = (now - requested_at).total_seconds() / 3600
    return elapsed >= settings.approval_timeout_hours


def should_send_pki_reminder(
    sent_at: _dt.datetime,
    reminders_sent: int,
    now_fn: Callable[[], _dt.datetime] | None = None,
) -> tuple[bool, int]:
    """Returns (should_send, new_reminders_sent) for PKI reply reminders.
    
    Reminder schedule: send at 24h and 72h after initial email.
    """
    now = (now_fn or (lambda: _dt.datetime.now(_dt.timezone.utc)))()
    elapsed_hours = (now - sent_at).total_seconds() / 3600
    reminder_thresholds = [24, 72]
    
    for i, threshold in enumerate(reminder_thresholds):
        if reminders_sent <= i and elapsed_hours >= threshold:
            return True, reminders_sent + 1
    return False, reminders_sent
```

---

## Load Test Requirements

The load test simulates an expiry wave of 100 certificates:

```python
# tests/test_performance.py

@pytest.mark.performance
async def test_expiry_wave_100_certs():
    """
    Simulate 100 simultaneous SSL-expiry alerts.
    Assert:
    - All 100 reach a terminal state within the configured time window
    - Concurrent children never exceed MAX_CONCURRENT_RENEWALS (default 20)
    - Zero duplicate Jira tickets (idempotency check)
    - Zero duplicate emails to PKI mailbox
    - Zero PKI/Jira/SNOW rate-limit breaches
    - At least 95 complete successfully (5% failure budget for transient errors)
    """
    ...

@pytest.mark.performance
async def test_batch_throughput():
    """
    Submit batches of 20 certs every 10 seconds for 1 minute.
    Assert throughput >= 100 renewals/hour (≈ 1.67/second sustained).
    """
    ...

@pytest.mark.performance
async def test_no_duplicate_side_effects():
    """
    Simulate 10 retries of the same workflow_id alert.
    Assert exactly 1 Jira ticket, 1 PKI email, 1 ServiceNow CHG created.
    """
    ...
```

---

## Timer Tests

```python
# tests/test_timers.py

def test_approval_escalation_fires_at_48h():
    """should_escalate_approval returns True exactly at 48h boundary."""
    requested = _dt.datetime(2026, 7, 28, 10, 0, 0, tzinfo=_dt.timezone.utc)
    now_just_before = requested + _dt.timedelta(hours=47, minutes=59)
    now_at_boundary = requested + _dt.timedelta(hours=48)
    
    assert not should_escalate_approval(requested, now_fn=lambda: now_just_before)
    assert should_escalate_approval(requested, now_fn=lambda: now_at_boundary)

def test_pki_reminder_at_24h_and_72h():
    """Reminders sent at 24h and 72h after PKI email, then no more."""
    sent = _dt.datetime(2026, 7, 28, 10, 0, 0, tzinfo=_dt.timezone.utc)
    
    should, count = should_send_pki_reminder(sent, 0, now_fn=lambda: sent + _dt.timedelta(hours=25))
    assert should and count == 1
    
    should, count = should_send_pki_reminder(sent, 1, now_fn=lambda: sent + _dt.timedelta(hours=73))
    assert should and count == 2
    
    should, count = should_send_pki_reminder(sent, 2, now_fn=lambda: sent + _dt.timedelta(hours=100))
    assert not should and count == 2  # no third reminder
```

---

## Cosmos RU Sizing Guidance

- `workflow_state`: ~1 RU per read, ~5 RU per write; assume 20 concurrent renewals × 10 writes/renewal = 100 RU/s baseline
- `audit_log`: append-only; ~5 RU per event; 8 events/renewal × 100/hour = ~800 RU/hour (peak)
- Autoscale: start at 400 RU/s autoscale (scales to 4000 RU/s); set alert at 80% utilization
- Service Bus: standard tier for dev/uat; premium for prod (for isolation + higher throughput)

---

## Acceptance Criteria

- Load test: 0 duplicate side effects; throughput ≥ 100/hour; 100-cert wave drains in < 1 business day
- Timer tests: approval escalation fires at exactly 48h; PKI reminders at 24h and 72h; no more after that
- Idempotency: second call with same idempotency key returns prior result without calling the external API
- Rate limiters: no PKI/Jira/SNOW quota breaches in load test

---

## Verification

```bash
pytest tests/test_timers.py -v
pytest tests/test_performance.py -v -m performance --timeout=300
```
