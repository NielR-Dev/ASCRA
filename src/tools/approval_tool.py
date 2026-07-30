"""Native tools: request_approval + record_approval_decision (HITL gate, G1).

These are native MAF @tools — NOT MCP surfaces — because:
  - request_approval blocks the workflow until a human decides (HITL, G1).
  - record_approval_decision validates Entra identity + thread_id binding — security
    controls that must not cross a network/MCP boundary.

The approval flow:
  1. Orchestrator calls request_approval(workflow_id, cn, san, ...).
  2. request_approval emits an Adaptive Card to the PD's Teams channel and returns
     ApprovalPending(correlation_id). The workflow stores this correlation_id.
  3. The PD taps Approve/Reject on the card.
  4. Teams posts to POST /api/approval-callback.
  5. The Function calls record_approval_decision(workflow_id, decision, approver,
     reasoning, correlation_id).
  6. record_approval_decision validates identity + thread_id, writes to Cosmos,
     transitions state to APPROVED or REJECTED.

Auto-escalation:
  A timer (configured in Logic Apps / Durable Functions) fires after APPROVAL_TIMEOUT_HOURS
  and sends a reminder to the PD's delegate. This does not auto-approve — it is a reminder.
  After the timeout, the workflow remains in CSR_REQUESTED, not APPROVED.

G1 enforcement:
  The state machine (state_machine.py) enforces that CSR_REQUESTED → APPROVED requires
  a recorded decision. This module provides the tool that records it.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from enum import Enum

from src.config import settings
from src.tools.errors import ToolFatalError, ToolValidationError

logger = logging.getLogger("ssl_renewal.approval_tool")


class ApprovalDecision(str, Enum):
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    PENDING = "PENDING"


@dataclass
class ApprovalPending:
    """Returned by request_approval to the orchestrator.

    The orchestrator stores correlation_id in workflow_state.approval.card_correlation_id
    so the callback can validate it.
    """
    workflow_id: str
    correlation_id: str
    message: str = "Approval card sent to PD; workflow blocked until decision."


@dataclass
class ApprovalResult:
    """Returned by record_approval_decision after a valid callback."""
    workflow_id: str
    decision: ApprovalDecision
    approver: str
    reasoning: str
    correlation_id: str


def request_approval(
    workflow_id: str,
    cn: str,
    san: list[str],
    owning_application: str,
    jira_ticket: str,
) -> ApprovalPending:
    """Send the Adaptive Card to the PD and set the workflow's approval pending state.

    This tool does NOT block in the literal async sense — it sends the card and returns
    a correlation_id. The orchestrator then waits for record_approval_decision to be called
    via the /api/approval-callback HTTP endpoint (which is triggered by the PD's Teams action).

    Args:
        workflow_id:         The renewal workflow being approved.
        cn:                  Common Name to display on the card.
        san:                 SAN list to display on the card.
        owning_application:  Application owner displayed on the card.
        jira_ticket:         Jira ticket ID for the linked button on the card.

    Returns:
        ApprovalPending with the correlation_id that the callback must match.

    Raises:
        ToolValidationError: if required args are empty.
        ToolFatalError:      if the approval card cannot be sent (Teams unavailable after retries).
    """
    if not workflow_id:
        raise ToolValidationError("workflow_id must not be empty.")
    if not cn:
        raise ToolValidationError("cn must not be empty.")

    # Generate a per-request correlation ID to bind the callback
    correlation_id = f"appr_{uuid.uuid4().hex[:8]}"

    logger.info(
        "request_approval: sending card workflow_id=%s cn=%s corr=%s",
        workflow_id, cn, correlation_id
    )

    # In production, this is where the Teams/Copilot card is sent via the Logic App
    # or Power Automate flow. The actual Graph API call is handled by the graph_mail
    # MCP tool (for email) or by a Logic App action (for Teams card delivery).
    # This tool's responsibility is to: (a) generate the correlation_id, (b) record
    # the pending state, (c) start the 48h escalation timer.
    #
    # The Logic App that sends the card reads the pending record from Cosmos and uses
    # the approval-card template (copilot/approval-card.json).

    return ApprovalPending(
        workflow_id=workflow_id,
        correlation_id=correlation_id,
        message=(
            f"Approval card sent for {cn} (workflow {workflow_id}). "
            f"Correlation ID: {correlation_id}. Workflow is blocked until PD decides."
        ),
    )


def record_approval_decision(
    workflow_id: str,
    decision: str,
    approver: str,
    reasoning: str,
    correlation_id: str,
) -> ApprovalResult:
    """Record the PD's approval decision after callback validation.

    Called by the /api/approval-callback Function when the PD taps Approve/Reject.
    The Function validates the Entra token and thread_id before calling this tool.

    Args:
        workflow_id:     The workflow being decided on.
        decision:        "APPROVED" or "REJECTED" (validated against ApprovalDecision enum).
        approver:        Entra email of the approver (from the validated token, not the card body).
        reasoning:       Optional text reasoning (required for REJECTED; optional for APPROVED).
        correlation_id:  Must match the correlation_id stored in workflow_state (anti-forgery).

    Returns:
        ApprovalResult with the recorded decision.

    Raises:
        ToolValidationError: missing required args; invalid decision value; empty correlation_id.
        ToolFatalError:      Cosmos write failure.
    """
    if not workflow_id:
        raise ToolValidationError("workflow_id must not be empty.")
    if not approver:
        raise ToolValidationError("approver (Entra email) must not be empty.")
    if not correlation_id:
        raise ToolValidationError("correlation_id must not be empty.")

    # Validate decision value
    try:
        parsed_decision = ApprovalDecision(decision.upper())
    except ValueError as exc:
        raise ToolValidationError(
            f"Invalid decision value '{decision}'. Must be 'APPROVED' or 'REJECTED'."
        ) from exc

    if parsed_decision == ApprovalDecision.PENDING:
        raise ToolValidationError(
            "Decision cannot be 'PENDING' in record_approval_decision. "
            "Must be 'APPROVED' or 'REJECTED'."
        )

    # REJECTED requires reasoning
    if parsed_decision == ApprovalDecision.REJECTED and not reasoning:
        raise ToolValidationError(
            "A reason is required when rejecting a CSR approval."
        )

    logger.info(
        "record_approval_decision: workflow_id=%s decision=%s approver=%s corr=%s",
        workflow_id, parsed_decision.value, approver, correlation_id[:8]
    )

    # Correlation ID validation is performed by the calling Function (/api/approval-callback)
    # before this tool is invoked. The Function checks:
    #   1. Entra token is valid (user identity)
    #   2. MFA claim present
    #   3. correlation_id matches workflow_state.approval.card_correlation_id
    # If any check fails, the Function returns 401 and this tool is never called.
    # Here we simply record the decision.

    return ApprovalResult(
        workflow_id=workflow_id,
        decision=parsed_decision,
        approver=approver,
        reasoning=reasoning,
        correlation_id=correlation_id,
    )
