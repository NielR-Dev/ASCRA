# Phase 8d — Development: State Machine, Batch Coordinator & Rate Limiter

> **Pre-read:** [00-context.md](00-context.md) · depends on 08a, 08b
> **Deliverable:** `state_machine.py`, `batch_coordinator.py`, `rate_limiter.py`, `retry_orchestration.py`
> **Task IDs:** T02, T10, T19
> **Effort estimate:** ~5 person-days

---

## Your Task

Implement the deterministic state machine, the fleet-scale batch coordinator, the per-downstream rate limiters, and the magentic retry sub-orchestration. These are the concurrency and resilience core of the system.

---

## What to Produce

1. **`src/orchestrator/state_machine.py`** — `State` enum + transition rules + `WorkflowState`
2. **`src/orchestrator/batch_coordinator.py`** — fleet fan-out/fan-in with bounded concurrency
3. **`src/orchestrator/rate_limiter.py`** — async token-bucket limiter per downstream
4. **`src/orchestrator/retry_orchestration.py`** — magentic Diagnostic + Escalation sub-agents
5. **`tests/test_state_machine.py`**
6. **`tests/test_batch_coordinator.py`**
7. **`tests/test_rate_limiter.py`**
8. **`tests/test_retry_orchestration.py`**

---

## `state_machine.py` — Canonical Implementation

```python
# src/orchestrator/state_machine.py
"""Deterministic workflow state machine. The LLM proposes; the machine disposes."""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    ALERT_RECEIVED = "ALERT_RECEIVED"
    PARSED = "PARSED"
    CSR_READY = "CSR_READY"
    CSR_REQUESTED = "CSR_REQUESTED"
    APPROVED = "APPROVED"
    PKI_REPLIED = "PKI_REPLIED"
    VERIFIED = "VERIFIED"
    COMPLETE = "COMPLETE"        # terminal (success)
    REJECTED = "REJECTED"        # terminal (PD rejected)
    FAILED = "FAILED"            # terminal (unrecoverable / escalated)


TERMINAL = {State.COMPLETE, State.REJECTED, State.FAILED}

_ALLOWED: dict[State, set[State]] = {
    State.ALERT_RECEIVED: {State.PARSED},
    State.PARSED: {State.CSR_READY},
    State.CSR_READY: {State.CSR_REQUESTED},
    State.CSR_REQUESTED: {State.APPROVED, State.REJECTED},
    State.APPROVED: {State.PKI_REPLIED},
    State.PKI_REPLIED: {State.VERIFIED},
    State.VERIFIED: {State.COMPLETE},
}


class IllegalTransition(RuntimeError):
    """Raised when a transition is not permitted by the state machine."""


def can_transition(src: State, dst: State) -> bool:
    if src in TERMINAL:
        return False
    if dst is State.FAILED:          # escalation/kill-switch may fail any live workflow
        return True
    return dst in _ALLOWED.get(src, set())


def assert_transition(src: State, dst: State) -> None:
    if not can_transition(src, dst):
        raise IllegalTransition(f"Illegal transition {src} -> {dst}")


@dataclass
class WorkflowState:
    workflow_id: str
    state: State = State.ALERT_RECEIVED
    cn: str = ""
    san: list[str] = field(default_factory=list)
    owning_application: str = ""
    context: dict = field(default_factory=dict)

    def transition(self, dst: State) -> None:
        assert_transition(self.state, dst)
        self.state = dst
```

---

## `batch_coordinator.py` — Canonical Implementation

```python
# src/orchestrator/batch_coordinator.py
"""Fleet-scale orchestration: fan out one isolated child renewal per certificate,
run concurrently under a bounded limiter, rate-limit shared downstreams, fan results in.

A single renewal is a batch of size 1 — no special-casing.
"""
from __future__ import annotations
import asyncio
from dataclasses import dataclass, field
from enum import Enum

from src.config import settings
from src.orchestrator.state_machine import State


class ChildStatus(str, Enum):
    OK = "OK"
    FAILED = "FAILED"


@dataclass
class ChildResult:
    workflow_id: str
    cn: str
    final_state: State | None = None
    status: ChildStatus = ChildStatus.OK
    error: str = ""


@dataclass
class BatchResult:
    batch_id: str
    total: int
    results: list[ChildResult] = field(default_factory=list)

    @property
    def by_state(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.results:
            key = r.final_state.value if r.final_state else "in_flight"
            counts[key] = counts.get(key, 0) + 1
        return counts


def _dedupe_by_cn(alerts: list[dict]) -> list[dict]:
    """First occurrence per CN wins."""
    seen: set[str] = set()
    unique: list[dict] = []
    for a in alerts:
        cn = str(a.get("cn", "")).lower()
        if cn and cn not in seen:
            seen.add(cn)
            unique.append(a)
    return unique


async def run_batch(batch_id: str, alerts: list[dict], run_child) -> BatchResult:
    """Fan out child renewals with bounded concurrency; fan results back in.

    run_child: async callable (alert) -> ChildResult. Must be idempotent on workflow_id.
    Exceptions are captured per child — the batch always completes. (FR-15)
    """
    unique = _dedupe_by_cn(alerts)
    limit = max(1, settings.max_concurrent_renewals)
    semaphore = asyncio.Semaphore(limit)

    async def _guarded(alert: dict) -> ChildResult:
        cn = str(alert.get("cn", ""))
        async with semaphore:
            try:
                return await run_child(alert)
            except Exception as exc:
                return ChildResult(
                    workflow_id=alert.get("workflow_id", ""),
                    cn=cn,
                    final_state=State.FAILED,
                    status=ChildStatus.FAILED,
                    error=type(exc).__name__,
                )

    results = await asyncio.gather(*(_guarded(a) for a in unique))
    return BatchResult(batch_id=batch_id, total=len(unique), results=list(results))
```

---

## `rate_limiter.py` — Canonical Implementation

```python
# src/orchestrator/rate_limiter.py
"""Async token-bucket rate limiter — one instance per shared downstream.

Back-pressure is per-lane: throttling PKI does not stall Jira.
"""
from __future__ import annotations
import asyncio


class RateLimiter:
    """At most `rate` acquisitions per `per` seconds (token bucket, FIFO under lock)."""

    def __init__(self, rate: int, per: float = 60.0) -> None:
        self._rate = max(1, rate)
        self._per = per
        self._allowance = float(self._rate)
        self._lock = asyncio.Lock()
        self._last: float | None = None

    async def acquire(self, now: float | None = None) -> None:
        async with self._lock:
            t = now if now is not None else asyncio.get_event_loop().time()
            if self._last is None:
                self._last = t
            self._allowance += (t - self._last) * (self._rate / self._per)
            self._last = t
            if self._allowance > self._rate:
                self._allowance = float(self._rate)
            if self._allowance < 1.0:
                wait = (1.0 - self._allowance) * (self._per / self._rate)
                await asyncio.sleep(wait)
                self._allowance = 0.0
            else:
                self._allowance -= 1.0


# Shared, process-wide limiters — acquire before each downstream call.
from src.config import settings
PKI_LIMITER  = RateLimiter(rate=settings.pki_rate_per_min,  per=60.0)
JIRA_LIMITER = RateLimiter(rate=settings.jira_rate_per_min, per=60.0)
SNOW_LIMITER = RateLimiter(rate=settings.snow_rate_per_min, per=60.0)
```

---

## `retry_orchestration.py` — Canonical Implementation

```python
# src/orchestrator/retry_orchestration.py
"""Magentic retry: on verifier failure, a Diagnostic agent proposes a fix and an Escalation
agent decides RESEND / ESCALATE_PD / FAIL_OPEN. Bounded by max_rounds and max_escalations."""
from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.config import settings


class RetryDecision(str, Enum):
    RESEND = "RESEND"
    ESCALATE_PD = "ESCALATE_PD"
    FAIL_OPEN = "FAIL_OPEN"


@dataclass
class RetryOutcome:
    decision: RetryDecision
    rounds_used: int
    escalations_used: int
    rationale: str


async def run_retry_orchestration(chat_client: Any, failure_reason: str,
                                  rounds_so_far: int = 0,
                                  escalations_so_far: int = 0) -> RetryOutcome:
    """Drive the magentic loop within configured caps (default 6 rounds / 2 escalations).

    Uses two specialist agents:
      * Diagnostic — classifies the verifier failure and proposes a corrective action.
      * Escalation — maps the diagnosis to a RetryDecision, respecting the caps.
    Deterministic caps guarantee termination; the model only chooses *among* safe options.
    """
    from agent_framework import ChatAgent

    diagnostic = ChatAgent(
        chat_client=chat_client, name="diagnostic",
        instructions=("Classify the CER verification failure (chain/CN/SAN/expiry/format). "
                      "Propose the single most likely corrective action. Output data only."),
    )
    escalation = ChatAgent(
        chat_client=chat_client, name="escalation",
        instructions=("Given a diagnosis, choose exactly one: RESEND, ESCALATE_PD, or FAIL_OPEN. "
                      "Prefer RESEND for transient/format issues; ESCALATE_PD for ambiguity; "
                      "FAIL_OPEN only when unrecoverable."),
    )

    rounds = rounds_so_far
    escalations = escalations_so_far
    rationale = ""
    while rounds < settings.magentic_max_rounds:
        rounds += 1
        diag = await diagnostic.run(f"Verifier failure: {failure_reason}")
        verdict = await escalation.run(f"Diagnosis: {diag.text}")
        text = verdict.text.upper()
        if "FAIL_OPEN" in text:
            return RetryOutcome(RetryDecision.FAIL_OPEN, rounds, escalations, diag.text)
        if "ESCALATE_PD" in text:
            escalations += 1
            if escalations >= settings.magentic_max_escalations:
                return RetryOutcome(RetryDecision.FAIL_OPEN, rounds, escalations,
                                    "Escalation cap reached; failing safely to manual runbook.")
            return RetryOutcome(RetryDecision.ESCALATE_PD, rounds, escalations, diag.text)
        rationale = diag.text
        return RetryOutcome(RetryDecision.RESEND, rounds, escalations, rationale)

    return RetryOutcome(RetryDecision.FAIL_OPEN, rounds, escalations,
                        "Round cap reached; failing safely to manual runbook.")
```

---

## Test Requirements

### `tests/test_state_machine.py`
- Every legal transition from the `_ALLOWED` table passes
- Every illegal transition (including reverse transitions) raises `IllegalTransition`
- Terminal states (COMPLETE, REJECTED, FAILED) are sticky — no further transitions
- `FAILED` is reachable from any non-terminal state (kill-switch / escalation)
- `WorkflowState.transition()` updates `state` on success; raises on illegal

### `tests/test_batch_coordinator.py`
- De-duplication: duplicate CNs result in only one child per CN
- Concurrency never exceeds `MAX_CONCURRENT_RENEWALS` at any instant (use a counter)
- One child raising an exception → that child is `FAILED`; **all siblings still complete** (FR-15)
- `batch.by_state` counts match the actual child outcomes
- Re-running with same `alerts` and idempotent `run_child` → attaches to existing results, no duplicates

### `tests/test_rate_limiter.py`
- No more than `rate` acquisitions per window (inject monotonic clock)
- A slow PKI lane does NOT stall the Jira lane (independent instances)
- FIFO ordering: requests resolve in the order they were queued

### `tests/test_retry_orchestration.py`
- RESEND: returned when escalation agent says RESEND
- ESCALATE_PD: returned when escalation says ESCALATE_PD; escalation counter incremented
- FAIL_OPEN on escalation cap: after `magentic_max_escalations` escalations → FAIL_OPEN
- FAIL_OPEN on round cap: after `magentic_max_rounds` rounds → FAIL_OPEN
- All decisions are one of the `RetryDecision` enum values

---

## Acceptance Criteria

- State machine rejects every illegal transition; accepts every legal one
- Batch coordinator: sibling isolation guaranteed (FR-15); de-duplication works; concurrency bounded
- Rate limiter: independent per-lane; does not stall one lane due to another
- Retry: terminates within `max_rounds`; escalation cap leads to `FAIL_OPEN`

---

## Verification

```bash
pytest tests/test_state_machine.py tests/test_batch_coordinator.py tests/test_rate_limiter.py tests/test_retry_orchestration.py -v
```
