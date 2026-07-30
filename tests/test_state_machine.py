"""Tests for the state machine (T02).

Tests cover:
  - All legal transitions pass (happy path + rejection + escalation)
  - All illegal transitions raise IllegalTransition
  - Terminal states are sticky (no transitions out of COMPLETE/REJECTED/FAILED)
  - FAILED is reachable from any non-terminal state
  - WorkflowState.transition() uses assert_transition correctly
"""
from __future__ import annotations

import pytest
from src.orchestrator.state_machine import (
    TERMINAL,
    IllegalTransition,
    State,
    WorkflowState,
    assert_transition,
    can_transition,
)


# ---------------------------------------------------------------------------
# can_transition / assert_transition
# ---------------------------------------------------------------------------

class TestCanTransition:
    # Happy-path forward transitions
    @pytest.mark.parametrize("src,dst", [
        (State.ALERT_RECEIVED, State.PARSED),
        (State.PARSED, State.CSR_READY),
        (State.CSR_READY, State.CSR_REQUESTED),
        (State.CSR_REQUESTED, State.APPROVED),
        (State.CSR_REQUESTED, State.REJECTED),
        (State.APPROVED, State.PKI_REPLIED),
        (State.PKI_REPLIED, State.VERIFIED),
        (State.VERIFIED, State.COMPLETE),
    ])
    def test_legal_forward_transitions(self, src: State, dst: State) -> None:
        assert can_transition(src, dst) is True

    # FAILED reachable from any live state
    @pytest.mark.parametrize("src", [
        State.ALERT_RECEIVED,
        State.PARSED,
        State.CSR_READY,
        State.CSR_REQUESTED,
        State.APPROVED,
        State.PKI_REPLIED,
        State.VERIFIED,
    ])
    def test_failed_reachable_from_any_live_state(self, src: State) -> None:
        assert can_transition(src, State.FAILED) is True

    # Terminal states cannot transition anywhere
    @pytest.mark.parametrize("terminal", [
        State.COMPLETE,
        State.REJECTED,
        State.FAILED,
    ])
    def test_terminal_states_cannot_transition(self, terminal: State) -> None:
        for dst in State:
            assert can_transition(terminal, dst) is False

    # Illegal skips
    @pytest.mark.parametrize("src,dst", [
        (State.ALERT_RECEIVED, State.APPROVED),     # skip PARSED, CSR_READY, CSR_REQUESTED
        (State.ALERT_RECEIVED, State.COMPLETE),
        (State.PARSED, State.APPROVED),             # skip CSR steps
        (State.CSR_REQUESTED, State.VERIFIED),      # skip APPROVED, PKI_REPLIED
        (State.CSR_REQUESTED, State.COMPLETE),
        (State.APPROVED, State.COMPLETE),           # skip PKI_REPLIED, VERIFIED
        (State.APPROVED, State.VERIFIED),           # skip PKI_REPLIED
    ])
    def test_illegal_skips_cannot_transition(self, src: State, dst: State) -> None:
        assert can_transition(src, dst) is False

    # Backward transitions are illegal
    @pytest.mark.parametrize("src,dst", [
        (State.PARSED, State.ALERT_RECEIVED),
        (State.APPROVED, State.CSR_REQUESTED),
        (State.VERIFIED, State.APPROVED),
    ])
    def test_backward_transitions_illegal(self, src: State, dst: State) -> None:
        assert can_transition(src, dst) is False


class TestAssertTransition:
    def test_legal_transition_does_not_raise(self) -> None:
        assert_transition(State.ALERT_RECEIVED, State.PARSED)  # must not raise

    def test_illegal_transition_raises(self) -> None:
        with pytest.raises(IllegalTransition):
            assert_transition(State.ALERT_RECEIVED, State.COMPLETE)

    def test_illegal_transition_message_contains_states(self) -> None:
        with pytest.raises(IllegalTransition, match="ALERT_RECEIVED"):
            assert_transition(State.ALERT_RECEIVED, State.VERIFIED)


# ---------------------------------------------------------------------------
# WorkflowState
# ---------------------------------------------------------------------------

class TestWorkflowState:
    def test_initial_state_is_alert_received(self) -> None:
        ws = WorkflowState(workflow_id="wf_001")
        assert ws.state == State.ALERT_RECEIVED

    def test_legal_transition_updates_state(self) -> None:
        ws = WorkflowState(workflow_id="wf_001")
        ws.transition(State.PARSED)
        assert ws.state == State.PARSED

    def test_chain_of_transitions(self) -> None:
        ws = WorkflowState(workflow_id="wf_002")
        for dst in [
            State.PARSED,
            State.CSR_READY,
            State.CSR_REQUESTED,
            State.APPROVED,
            State.PKI_REPLIED,
            State.VERIFIED,
            State.COMPLETE,
        ]:
            ws.transition(dst)
        assert ws.state == State.COMPLETE

    def test_illegal_transition_raises(self) -> None:
        ws = WorkflowState(workflow_id="wf_003")
        ws.transition(State.PARSED)
        with pytest.raises(IllegalTransition):
            ws.transition(State.COMPLETE)  # Skip

    def test_terminal_complete_is_sticky(self) -> None:
        ws = WorkflowState(workflow_id="wf_004")
        for dst in [State.PARSED, State.CSR_READY, State.CSR_REQUESTED,
                    State.APPROVED, State.PKI_REPLIED, State.VERIFIED, State.COMPLETE]:
            ws.transition(dst)
        with pytest.raises(IllegalTransition):
            ws.transition(State.ALERT_RECEIVED)

    def test_terminal_rejected_is_sticky(self) -> None:
        ws = WorkflowState(workflow_id="wf_005")
        ws.transition(State.PARSED)
        ws.transition(State.CSR_READY)
        ws.transition(State.CSR_REQUESTED)
        ws.transition(State.REJECTED)
        with pytest.raises(IllegalTransition):
            ws.transition(State.APPROVED)

    def test_failed_reachable_from_approved(self) -> None:
        ws = WorkflowState(workflow_id="wf_006")
        ws.transition(State.PARSED)
        ws.transition(State.CSR_READY)
        ws.transition(State.CSR_REQUESTED)
        ws.transition(State.APPROVED)
        ws.transition(State.FAILED)  # Kill-switch / escalation
        assert ws.state == State.FAILED

    def test_is_terminal_property(self) -> None:
        ws = WorkflowState(workflow_id="wf_007")
        assert ws.is_terminal is False
        ws.transition(State.PARSED)
        ws.transition(State.CSR_READY)
        ws.transition(State.CSR_REQUESTED)
        ws.transition(State.REJECTED)
        assert ws.is_terminal is True

    def test_terminal_set_contains_correct_states(self) -> None:
        assert State.COMPLETE in TERMINAL
        assert State.REJECTED in TERMINAL
        assert State.FAILED in TERMINAL
        assert State.APPROVED not in TERMINAL
        assert State.PARSED not in TERMINAL
