"""Timers for approval auto-escalation and PKI reminder emails.

All timers use an injected clock (now_fn) for testability — never call
datetime.now() directly in timer logic. This makes the timers fully
deterministic in tests without sleep() or real-time waiting.

Used by:
  - The `orchestrate` Function's status-check path (timer trigger every 15 min)
  - The batch coordinator's timeout monitor
"""
from __future__ import annotations

import datetime as _dt
from typing import Callable

from src.config import settings

# Type alias for the clock function
NowFn = Callable[[], _dt.datetime]

_DEFAULT_NOW: NowFn = lambda: _dt.datetime.now(_dt.timezone.utc)


def should_escalate_approval(
    requested_at: _dt.datetime,
    now_fn: NowFn | None = None,
) -> bool:
    """Return True if the approval timeout has elapsed without a decision (G1).

    Fires when ``APPROVAL_TIMEOUT_HOURS`` has passed since the approval was
    requested and no Approve/Reject decision has been recorded.

    The orchestrator calls this on each status-check cycle; if True, it sends
    a second Teams card to the PD delegate and logs an ``approval_timeout`` event.

    Args:
        requested_at: Timezone-aware datetime when the approval request was sent.
        now_fn:        Clock function for testing; defaults to UTC now.

    Returns:
        True if ``(now - requested_at).hours >= approval_timeout_hours``.
    """
    now = (now_fn or _DEFAULT_NOW)()
    elapsed_hours = (now - requested_at).total_seconds() / 3600
    return elapsed_hours >= settings.approval_timeout_hours


def should_send_pki_reminder(
    sent_at: _dt.datetime,
    reminders_sent: int,
    now_fn: NowFn | None = None,
) -> tuple[bool, int]:
    """Return (should_send, new_reminders_sent) for PKI reply reminders.

    Reminder schedule:
      - Reminder 1: 24 hours after initial PKI email (if no reply yet)
      - Reminder 2: 72 hours after initial PKI email (if still no reply)
      - No further automatic reminders (manual escalation after this)

    Args:
        sent_at:        Timezone-aware datetime when the PKI email was sent.
        reminders_sent: Count of reminders already sent for this workflow.
        now_fn:         Clock function for testing; defaults to UTC now.

    Returns:
        Tuple of:
          - bool: True if a reminder should be sent now.
          - int:  Updated reminders_sent count (reminders_sent + 1 if sending).
    """
    now = (now_fn or _DEFAULT_NOW)()
    elapsed_hours = (now - sent_at).total_seconds() / 3600
    reminder_thresholds = [24, 72]   # hours after initial email

    for i, threshold in enumerate(reminder_thresholds):
        if reminders_sent <= i and elapsed_hours >= threshold:
            return True, reminders_sent + 1
    return False, reminders_sent


def is_pki_overdue(
    sent_at: _dt.datetime,
    now_fn: NowFn | None = None,
) -> bool:
    """Return True if PKI has not replied within the configured wait period.

    Fires when ``pki_reply_wait_days`` business days have elapsed since the
    PKI email was sent. When True, the orchestrator escalates to SRE + PD.

    Args:
        sent_at:  Timezone-aware datetime when the PKI email was sent.
        now_fn:   Clock function for testing; defaults to UTC now.

    Returns:
        True if ``(now - sent_at).days >= pki_reply_wait_days``.
    """
    now = (now_fn or _DEFAULT_NOW)()
    elapsed_days = (now - sent_at).total_seconds() / 86400
    return elapsed_days >= settings.pki_reply_wait_days


def approval_deadline(requested_at: _dt.datetime) -> _dt.datetime:
    """Return the timezone-aware datetime at which approval escalation fires.

    Useful for including the deadline in the Teams approval card.
    """
    return requested_at + _dt.timedelta(hours=settings.approval_timeout_hours)


def pki_deadline(sent_at: _dt.datetime) -> _dt.datetime:
    """Return the timezone-aware datetime at which PKI is considered overdue."""
    return sent_at + _dt.timedelta(days=settings.pki_reply_wait_days)
