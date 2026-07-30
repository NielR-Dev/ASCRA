"""Magentic retry sub-orchestration (T10, FR-10).

When verify_cer returns pass_=False, the main orchestrator delegates to this module
to classify the failure and decide the corrective action. The decision is bounded
by deterministic caps (max_rounds, max_escalations) — the model only chooses
*among* safe options.

Two specialist agents:
  - Diagnostic: classifies the CER verification failure and proposes a corrective action.
  - Escalation: maps the diagnosis to a RetryDecision (RESEND / ESCALATE_PD / FAIL_OPEN).

Guaranteed termination:
  The while loop is bounded by max_rounds (default 6).
  ESCALATE_PD decisions are bounded by max_escalations (default 2).
  After either cap is reached, FAIL_OPEN is returned unconditionally.
  FAIL_OPEN → state transitions to FAILED (terminal); manual runbook is invoked.

This is advisory: the magentic agents suggest; the orchestrator + state machine act.
A RESEND decision by the magentic loop means the orchestrator will re-send the PKI
email with an idempotency key (to prevent duplicate emails on replay).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum
from typing import Any

from src.config import settings

logger = logging.getLogger("ssl_renewal.retry_orchestration")


class RetryDecision(str, Enum):
    RESEND = "RESEND"              # Re-request the certificate from PKI (fixable error)
    ESCALATE_PD = "ESCALATE_PD"   # Human judgment needed (ambiguous failure)
    FAIL_OPEN = "FAIL_OPEN"       # Give up safely → FAILED state → manual runbook


@dataclass
class RetryOutcome:
    """Result of a magentic retry sub-orchestration run."""
    decision: RetryDecision
    rounds_used: int
    escalations_used: int
    rationale: str


async def run_retry_orchestration(
    chat_client: Any,
    failure_reason: str,
    rounds_so_far: int = 0,
    escalations_so_far: int = 0,
) -> RetryOutcome:
    """Drive the magentic diagnostic loop within configured caps.

    Args:
        chat_client:       Foundry chat client (same one used by the main orchestrator).
        failure_reason:    The reason string from VerifyResult (e.g. "cn_match; san_match failed").
        rounds_so_far:     Rounds already used (for resumption after a restart).
        escalations_so_far: Escalations already used.

    Returns:
        RetryOutcome with the final decision and usage counts.
    """
    try:
        from agent_framework import ChatAgent
    except ImportError as exc:
        raise ImportError("agent_framework package is required.") from exc

    diagnostic_agent = ChatAgent(
        chat_client=chat_client,
        name="ssl_renewal_diagnostic",
        instructions=(
            "You are a specialist at classifying TLS certificate verification failures. "
            "Given a failure reason string from verify_cer, classify it into one of: "
            "CN_MISMATCH, SAN_MISMATCH, EXPIRED, SHORT_VALIDITY, FORMAT_ERROR, CHAIN_ERROR. "
            "Propose the single most likely corrective action. "
            "Output factual diagnosis only. Do not suggest bypassing any check."
        ),
    )

    escalation_agent = ChatAgent(
        chat_client=chat_client,
        name="ssl_renewal_escalation",
        instructions=(
            "Given a diagnosis of a CER verification failure, choose exactly one: "
            "RESEND (re-request the cert — use for transient or format issues), "
            "ESCALATE_PD (human judgment needed — use for ambiguous failures), "
            "FAIL_OPEN (unrecoverable — use when retrying would not help). "
            "Output only one of these three words as your decision."
        ),
    )

    rounds = rounds_so_far
    escalations = escalations_so_far

    while rounds < settings.magentic_max_rounds:
        rounds += 1

        logger.info(
            "retry_orchestration: round=%d escalations=%d failure_reason='%s'",
            rounds, escalations, failure_reason[:100]
        )

        diag_response = await diagnostic_agent.run(
            f"verify_cer failure: {failure_reason}"
        )
        diagnosis_text: str = diag_response.text

        escalation_response = await escalation_agent.run(
            f"Diagnosis: {diagnosis_text}\nDecide: RESEND, ESCALATE_PD, or FAIL_OPEN."
        )
        decision_text: str = escalation_response.text.upper()

        if "FAIL_OPEN" in decision_text:
            logger.warning(
                "retry_orchestration: FAIL_OPEN decision round=%d reason='%s'",
                rounds, diagnosis_text[:100]
            )
            return RetryOutcome(
                decision=RetryDecision.FAIL_OPEN,
                rounds_used=rounds,
                escalations_used=escalations,
                rationale=diagnosis_text,
            )

        if "ESCALATE_PD" in decision_text:
            escalations += 1
            if escalations >= settings.magentic_max_escalations:
                logger.warning(
                    "retry_orchestration: escalation cap reached (%d), forcing FAIL_OPEN",
                    escalations
                )
                return RetryOutcome(
                    decision=RetryDecision.FAIL_OPEN,
                    rounds_used=rounds,
                    escalations_used=escalations,
                    rationale="Escalation cap reached; failing safely to manual runbook.",
                )
            logger.info(
                "retry_orchestration: ESCALATE_PD decision round=%d escalations=%d",
                rounds, escalations
            )
            return RetryOutcome(
                decision=RetryDecision.ESCALATE_PD,
                rounds_used=rounds,
                escalations_used=escalations,
                rationale=diagnosis_text,
            )

        # RESEND (or unrecognised decision defaults to RESEND as the safer option)
        logger.info(
            "retry_orchestration: RESEND decision round=%d rationale='%s'",
            rounds, diagnosis_text[:100]
        )
        return RetryOutcome(
            decision=RetryDecision.RESEND,
            rounds_used=rounds,
            escalations_used=escalations,
            rationale=diagnosis_text,
        )

    # Round cap reached
    logger.warning(
        "retry_orchestration: round cap reached (%d), forcing FAIL_OPEN",
        settings.magentic_max_rounds
    )
    return RetryOutcome(
        decision=RetryDecision.FAIL_OPEN,
        rounds_used=rounds,
        escalations_used=escalations,
        rationale="Round cap reached; failing safely to manual runbook.",
    )
