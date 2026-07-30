"""Fleet-scale batch orchestration: fan-out/fan-in over child renewals (T19, FR-12–15).

Architecture:
  - The Batch Coordinator fans out one isolated child renewal workflow per unique CN.
  - Children run concurrently under a bounded asyncio.Semaphore (MAX_CONCURRENT_RENEWALS).
  - Per-downstream rate limiters (rate_limiter.py) are acquired inside each child.
  - Fan-in aggregates per-child outcomes into a BatchResult.
  - A single renewal is a batch of size 1 — one code path, no special-casing.

Isolation (FR-15):
  - Children share no mutable state.
  - A child exception is caught and recorded as FAILED; siblings continue unaffected.
  - Re-running run_batch with the same alerts re-attaches to existing children
    (idempotent) instead of creating duplicates.

Durability:
  - In production, run_batch is hosted inside a Durable Function so the coordinator
    state survives a host restart and resumes from the batch record in Cosmos.
  - The run_child callable is responsible for idempotency: a second call with the
    same alert must re-attach to the existing workflow, not create a duplicate
    Jira ticket or ServiceNow CHG.
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Awaitable, Callable

from src.config import settings
from src.orchestrator.state_machine import State

logger = logging.getLogger("ssl_renewal.batch_coordinator")


class ChildStatus(str, Enum):
    OK = "OK"
    FAILED = "FAILED"


@dataclass
class ChildResult:
    """Outcome of a single child renewal workflow."""
    workflow_id: str
    cn: str
    final_state: State | None = None
    status: ChildStatus = ChildStatus.OK
    error: str = ""


@dataclass
class BatchResult:
    """Aggregated outcome of a full batch (expiry wave)."""
    batch_id: str
    total: int
    results: list[ChildResult] = field(default_factory=list)

    @property
    def by_state(self) -> dict[str, int]:
        """Return counts of children by final state."""
        counts: dict[str, int] = {}
        for r in self.results:
            key = r.final_state.value if r.final_state else "in_flight"
            counts[key] = counts.get(key, 0) + 1
        return counts

    @property
    def failed_count(self) -> int:
        return sum(1 for r in self.results if r.status == ChildStatus.FAILED)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.status == ChildStatus.OK)


def _dedupe_by_cn(alerts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """De-duplicate alerts by CN (first occurrence per CN wins).

    An expiry wave often fires multiple Dynatrace alerts for the same hostname.
    We only create one child workflow per unique CN (case-insensitive).
    """
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for alert in alerts:
        cn = str(alert.get("cn", "")).lower().strip()
        if cn and cn not in seen:
            seen.add(cn)
            unique.append(alert)
        elif not cn:
            logger.warning("batch_coordinator: alert has no cn field; skipping: %s", alert)
    return unique


async def run_batch(
    batch_id: str,
    alerts: list[dict[str, Any]],
    run_child: Callable[[dict[str, Any]], Awaitable[ChildResult]],
) -> BatchResult:
    """Fan out child renewals with bounded concurrency; fan results back in.

    Args:
        batch_id:   Unique identifier for this batch/wave (stored in Cosmos batch container).
        alerts:     Raw expiry alerts (may contain duplicates; de-duped by CN).
        run_child:  Async callable ``(alert) -> ChildResult`` that executes one full
                    T0–T8 renewal. Must be idempotent on the alert's cn/workflow_id:
                    a second call with the same alert returns the existing result.

    Returns:
        BatchResult with per-child outcomes and aggregate counts.

    Notes:
        - Concurrency is bounded by ``MAX_CONCURRENT_RENEWALS`` (default 20).
        - Per-downstream rate limiting is the responsibility of run_child (via
          PKI_LIMITER, JIRA_LIMITER, SNOW_LIMITER from rate_limiter.py).
        - A child exception is caught here and recorded as FAILED; siblings continue
          (FR-15: partial failure never aborts the batch).
    """
    unique_alerts = _dedupe_by_cn(alerts)
    total = len(unique_alerts)
    limit = max(1, settings.max_concurrent_renewals)
    semaphore = asyncio.Semaphore(limit)

    logger.info(
        "batch_coordinator: starting batch_id=%s total=%d (from %d raw alerts) concurrency=%d",
        batch_id, total, len(alerts), limit
    )

    async def _guarded(alert: dict[str, Any]) -> ChildResult:
        cn = str(alert.get("cn", ""))
        async with semaphore:
            try:
                result = await run_child(alert)
                logger.debug(
                    "batch_coordinator: child complete cn=%s state=%s",
                    cn, result.final_state
                )
                return result
            except Exception as exc:
                # FR-15: isolation — capture this child's failure and continue
                logger.error(
                    "batch_coordinator: child FAILED cn=%s error=%s: %s",
                    cn, type(exc).__name__, exc
                )
                workflow_id = str(alert.get("workflow_id", f"wf_unknown_{cn}"))
                return ChildResult(
                    workflow_id=workflow_id,
                    cn=cn,
                    final_state=State.FAILED,
                    status=ChildStatus.FAILED,
                    error=type(exc).__name__,
                )

    # Launch all children concurrently; return_exceptions=False is safe because
    # _guarded never raises (it catches all exceptions and returns a FAILED result).
    raw_results = await asyncio.gather(*(_guarded(a) for a in unique_alerts))
    results: list[ChildResult] = list(raw_results)

    batch_result = BatchResult(batch_id=batch_id, total=total, results=results)
    logger.info(
        "batch_coordinator: batch_id=%s complete total=%d failed=%d ok=%d by_state=%s",
        batch_id, total, batch_result.failed_count, batch_result.success_count,
        batch_result.by_state
    )
    return batch_result
