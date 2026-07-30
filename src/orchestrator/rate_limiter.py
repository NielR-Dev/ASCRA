"""Async token-bucket rate limiter — one instance per shared downstream (T19).

Used to prevent a large expiry wave from flooding the PKI mailbox, Jira,
or ServiceNow beyond their API quotas. Back-pressure is per-lane: throttling
the PKI lane does not stall Jira or ServiceNow.

Usage (child renewal workflow):
    await PKI_LIMITER.acquire()
    # ... call graph_mail.send(...)

Process-wide singletons (PKI_LIMITER, JIRA_LIMITER, SNOW_LIMITER) are instantiated
at module import time from settings. The rate (acquisitions per per-second window)
is configurable via environment variables (G8).

For testing: inject a custom RateLimiter with a controlled clock via the `now`
parameter to acquire(), rather than patching asyncio.get_event_loop().time().
"""
from __future__ import annotations

import asyncio
import logging

from src.config import settings

logger = logging.getLogger("ssl_renewal.rate_limiter")


class RateLimiter:
    """Simple, fair async token-bucket rate limiter.

    Allows at most ``rate`` acquisitions per ``per`` seconds.
    Under the lock, the FIFO ordering of coroutines provides fairness across
    child workflows competing for the same downstream lane.

    Algorithm: token bucket with linear refill.
      - ``_allowance`` accumulates tokens at rate / per per second.
      - Each acquire() consumes one token. If < 1 token available, wait.
      - Allowance is capped at ``rate`` to prevent burst overruns after idle.
    """

    def __init__(self, rate: int, per: float = 60.0) -> None:
        self._rate = max(1, rate)
        self._per = per
        self._allowance = float(self._rate)
        self._lock = asyncio.Lock()
        self._last: float | None = None  # monotonic; set on first use

    @property
    def rate(self) -> int:
        return self._rate

    @property
    def per(self) -> float:
        return self._per

    async def acquire(self, now: float | None = None) -> None:
        """Block until a token is available, then consume one.

        Args:
            now: optional monotonic time override (for testing). If None, uses
                 ``asyncio.get_event_loop().time()``.
        """
        async with self._lock:
            t = now if now is not None else asyncio.get_event_loop().time()
            if self._last is None:
                self._last = t

            elapsed = t - self._last
            self._last = t

            # Refill tokens based on elapsed time
            self._allowance += elapsed * (self._rate / self._per)
            # Cap to burst ceiling
            if self._allowance > self._rate:
                self._allowance = float(self._rate)

            if self._allowance < 1.0:
                # Need to wait for the next token to accumulate
                wait_time = (1.0 - self._allowance) * (self._per / self._rate)
                logger.debug(
                    "rate_limiter: waiting %.2fs (rate=%d/%.0fs, allowance=%.2f)",
                    wait_time, self._rate, self._per, self._allowance
                )
                await asyncio.sleep(wait_time)
                self._allowance = 0.0
            else:
                self._allowance -= 1.0


# ---------------------------------------------------------------------------
# Process-wide shared limiters (one per downstream)
# Instantiated from settings — not hard-coded (G8).
# ---------------------------------------------------------------------------

PKI_LIMITER = RateLimiter(rate=settings.pki_rate_per_min, per=60.0)
JIRA_LIMITER = RateLimiter(rate=settings.jira_rate_per_min, per=60.0)
SNOW_LIMITER = RateLimiter(rate=settings.snow_rate_per_min, per=60.0)
