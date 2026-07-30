"""Deterministic workflow state machine (T02).

The LLM proposes the next action; the state machine disposes by validating whether
the requested transition is legal. This is a core security control — an LLM error
or prompt-injection attempt cannot advance a workflow past a prohibited transition.

State transitions:
  ALERT_RECEIVED → PARSED → CSR_READY → CSR_REQUESTED → APPROVED → PKI_REPLIED → VERIFIED → COMPLETE
  CSR_REQUESTED → REJECTED (terminal, PD rejected)
  any live state → FAILED (terminal, kill-switch / escalation / unrecoverable error)
  COMPLETE, REJECTED, FAILED are terminal — no further transitions possible.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class State(str, Enum):
    """Canonical workflow states. String enum for JSON serialization compatibility."""
    ALERT_RECEIVED = "ALERT_RECEIVED"
    PARSED = "PARSED"
    CSR_READY = "CSR_READY"
    CSR_REQUESTED = "CSR_REQUESTED"
    APPROVED = "APPROVED"
    PKI_REPLIED = "PKI_REPLIED"
    VERIFIED = "VERIFIED"
    COMPLETE = "COMPLETE"      # terminal — successful completion
    REJECTED = "REJECTED"      # terminal — PD rejected
    FAILED = "FAILED"          # terminal — unrecoverable / escalated


# Terminal states: no further transitions are possible
TERMINAL: frozenset[State] = frozenset({State.COMPLETE, State.REJECTED, State.FAILED})

# Allowed forward transitions (happy path + PD rejection).
# FAILED is reachable from any non-terminal state (kill-switch or escalation).
_ALLOWED: dict[State, frozenset[State]] = {
    State.ALERT_RECEIVED: frozenset({State.PARSED}),
    State.PARSED:         frozenset({State.CSR_READY}),
    State.CSR_READY:      frozenset({State.CSR_REQUESTED}),
    State.CSR_REQUESTED:  frozenset({State.APPROVED, State.REJECTED}),
    State.APPROVED:       frozenset({State.PKI_REPLIED}),
    State.PKI_REPLIED:    frozenset({State.VERIFIED}),
    State.VERIFIED:       frozenset({State.COMPLETE}),
}


class IllegalTransition(RuntimeError):
    """Raised when a transition is not permitted by the state machine.

    This is a hard error — the orchestrator must not retry a state transition
    that the machine has rejected.
    """


def can_transition(src: State, dst: State) -> bool:
    """Return True if the transition from src to dst is permitted.

    FAILED is reachable from any non-terminal state (kill-switch / escalation path).
    Terminal states cannot transition anywhere.
    """
    if src in TERMINAL:
        return False
    if dst is State.FAILED:
        return True  # any live state can be force-failed
    return dst in _ALLOWED.get(src, frozenset())


def assert_transition(src: State, dst: State) -> None:
    """Raise IllegalTransition if the transition is not permitted."""
    if not can_transition(src, dst):
        raise IllegalTransition(
            f"Illegal state transition: {src.value} → {dst.value}. "
            "This transition is not defined in the canonical state machine. "
            "Ensure the orchestrator is following the workflow steps in order."
        )


@dataclass
class WorkflowState:
    """In-memory workflow state container.

    This is the runtime view of a renewal workflow. The authoritative persisted
    version lives in Cosmos DB (cosmos_repo.py / workflow_state container).

    workflow_id: unique identifier (e.g. "wf_2026-07-28_api.prod.example.com_7f3a")
    state:       current state in the machine
    cn:          Common Name (set after PARSED)
    san:         SAN list (set after PARSED)
    owning_application: CMDB application name (set after PARSED)
    context:     arbitrary key-value bag for other state (e.g. jira_ticket, corr_id)
    """
    workflow_id: str
    state: State = State.ALERT_RECEIVED
    cn: str = ""
    san: list[str] = field(default_factory=list)
    owning_application: str = ""
    context: dict = field(default_factory=dict)

    def transition(self, dst: State) -> None:
        """Attempt a state transition. Raises IllegalTransition if not allowed."""
        assert_transition(self.state, dst)
        self.state = dst

    @property
    def is_terminal(self) -> bool:
        """Return True if the workflow is in a terminal state."""
        return self.state in TERMINAL
