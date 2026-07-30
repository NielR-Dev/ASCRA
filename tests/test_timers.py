"""Tests for orchestrator timers — approval escalation + PKI reminders.

All tests use injected clock (now_fn) for determinism — no real-time waiting.
"""
from __future__ import annotations

import datetime as _dt
import pytest

from src.orchestrator.timers import (
    approval_deadline,
    is_pki_overdue,
    pki_deadline,
    should_escalate_approval,
    should_send_pki_reminder,
)


_UTC = _dt.timezone.utc

# Reference datetime: 2026-07-28 10:00 UTC
_BASE = _dt.datetime(2026, 7, 28, 10, 0, 0, tzinfo=_UTC)


# ---------------------------------------------------------------------------
# should_escalate_approval
# ---------------------------------------------------------------------------

class TestApprovalEscalation:
    def test_not_escalated_before_timeout(self) -> None:
        """Just before the 48h threshold → should_escalate = False."""
        just_before = _BASE + _dt.timedelta(hours=47, minutes=59)
        assert should_escalate_approval(_BASE, now_fn=lambda: just_before) is False

    def test_escalated_at_exact_timeout(self) -> None:
        """Exactly at the 48h boundary → should_escalate = True."""
        at_boundary = _BASE + _dt.timedelta(hours=48)
        assert should_escalate_approval(_BASE, now_fn=lambda: at_boundary) is True

    def test_escalated_well_past_timeout(self) -> None:
        """72 hours elapsed → still True."""
        far_past = _BASE + _dt.timedelta(hours=72)
        assert should_escalate_approval(_BASE, now_fn=lambda: far_past) is True

    def test_just_created_not_escalated(self) -> None:
        """0 elapsed → False."""
        assert should_escalate_approval(_BASE, now_fn=lambda: _BASE) is False

    def test_approval_deadline_computed_correctly(self) -> None:
        """Deadline = requested_at + approval_timeout_hours (default 48)."""
        deadline = approval_deadline(_BASE)
        expected = _BASE + _dt.timedelta(hours=48)
        assert deadline == expected


# ---------------------------------------------------------------------------
# should_send_pki_reminder
# ---------------------------------------------------------------------------

class TestPkiReminders:
    def test_no_reminder_before_24h(self) -> None:
        """23h elapsed, 0 reminders sent → no reminder."""
        now = _BASE + _dt.timedelta(hours=23)
        should, count = should_send_pki_reminder(_BASE, 0, now_fn=lambda: now)
        assert should is False
        assert count == 0

    def test_first_reminder_at_24h(self) -> None:
        """25h elapsed, 0 reminders sent → reminder 1."""
        now = _BASE + _dt.timedelta(hours=25)
        should, count = should_send_pki_reminder(_BASE, 0, now_fn=lambda: now)
        assert should is True
        assert count == 1

    def test_second_reminder_at_72h(self) -> None:
        """73h elapsed, 1 reminder already sent → reminder 2."""
        now = _BASE + _dt.timedelta(hours=73)
        should, count = should_send_pki_reminder(_BASE, 1, now_fn=lambda: now)
        assert should is True
        assert count == 2

    def test_no_reminder_after_both_sent(self) -> None:
        """100h elapsed, 2 reminders already sent → no more reminders."""
        now = _BASE + _dt.timedelta(hours=100)
        should, count = should_send_pki_reminder(_BASE, 2, now_fn=lambda: now)
        assert should is False
        assert count == 2

    def test_no_reminder_between_24h_and_72h_when_first_sent(self) -> None:
        """50h elapsed, 1 reminder already sent → no second reminder yet (threshold 72h)."""
        now = _BASE + _dt.timedelta(hours=50)
        should, count = should_send_pki_reminder(_BASE, 1, now_fn=lambda: now)
        assert should is False
        assert count == 1

    def test_pki_overdue_after_5_days(self) -> None:
        """6 days elapsed → overdue."""
        now = _BASE + _dt.timedelta(days=6)
        assert is_pki_overdue(_BASE, now_fn=lambda: now) is True

    def test_pki_not_overdue_before_5_days(self) -> None:
        """4 days elapsed → not overdue."""
        now = _BASE + _dt.timedelta(days=4)
        assert is_pki_overdue(_BASE, now_fn=lambda: now) is False

    def test_pki_deadline_computed_correctly(self) -> None:
        """pki_deadline = sent_at + pki_reply_wait_days (default 5)."""
        deadline = pki_deadline(_BASE)
        expected = _BASE + _dt.timedelta(days=5)
        assert deadline == expected
