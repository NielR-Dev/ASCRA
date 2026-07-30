"""Tests for approval_tool — HITL gate (G1, T07).

Covers:
  - request_approval returns ApprovalPending with a correlation_id
  - record_approval_decision validates decision enum, records APPROVED/REJECTED
  - record_approval_decision rejects missing approver (anti-forgery)
  - record_approval_decision requires reasoning on REJECTED
  - PENDING is not a valid decision for record_approval_decision
  - Invalid decision strings raise ToolValidationError
"""
from __future__ import annotations

import pytest

from src.tools.approval_tool import (
    ApprovalDecision,
    ApprovalPending,
    ApprovalResult,
    record_approval_decision,
    request_approval,
)
from src.tools.errors import ToolValidationError


# ---------------------------------------------------------------------------
# request_approval
# ---------------------------------------------------------------------------

class TestRequestApproval:
    def test_returns_approval_pending_with_correlation_id(self) -> None:
        result = request_approval(
            workflow_id="wf_test",
            cn="api.prod.example.com",
            san=["api.prod.example.com"],
            owning_application="Orders-API",
            jira_ticket="SSL-001",
        )
        assert isinstance(result, ApprovalPending)
        assert result.workflow_id == "wf_test"
        assert result.correlation_id.startswith("appr_")
        assert len(result.correlation_id) > 8  # appr_ + 8 hex chars

    def test_each_call_generates_unique_correlation_id(self) -> None:
        """Multiple requests for the same workflow produce unique correlation IDs."""
        r1 = request_approval("wf_multi", "api.example.com", [], "App", "SSL-100")
        r2 = request_approval("wf_multi", "api.example.com", [], "App", "SSL-100")
        assert r1.correlation_id != r2.correlation_id

    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ToolValidationError, match="workflow_id"):
            request_approval("", "api.example.com", [], "App", "SSL-001")

    def test_empty_cn_raises(self) -> None:
        with pytest.raises(ToolValidationError, match="cn"):
            request_approval("wf_001", "", [], "App", "SSL-001")


# ---------------------------------------------------------------------------
# record_approval_decision
# ---------------------------------------------------------------------------

class TestRecordApprovalDecision:
    def test_approved_decision(self) -> None:
        result = record_approval_decision(
            workflow_id="wf_001",
            decision="APPROVED",
            approver="pd@test-domain.com",
            reasoning="Matches CMDB owner + SANs",
            correlation_id="appr_abc12345",
        )
        assert isinstance(result, ApprovalResult)
        assert result.decision == ApprovalDecision.APPROVED
        assert result.approver == "pd@test-domain.com"

    def test_rejected_decision_with_reasoning(self) -> None:
        result = record_approval_decision(
            workflow_id="wf_002",
            decision="REJECTED",
            approver="pd@test-domain.com",
            reasoning="Wrong SAN list",
            correlation_id="appr_def67890",
        )
        assert result.decision == ApprovalDecision.REJECTED
        assert result.reasoning == "Wrong SAN list"

    def test_lowercase_decision_accepted(self) -> None:
        """Decision string is case-insensitive."""
        result = record_approval_decision(
            workflow_id="wf_003",
            decision="approved",
            approver="pd@test-domain.com",
            reasoning="",
            correlation_id="appr_xyz11111",
        )
        assert result.decision == ApprovalDecision.APPROVED

    def test_rejected_without_reasoning_raises(self) -> None:
        """G1: rejection must include reasoning."""
        with pytest.raises(ToolValidationError, match="reason"):
            record_approval_decision(
                workflow_id="wf_004",
                decision="REJECTED",
                approver="pd@test-domain.com",
                reasoning="",
                correlation_id="appr_zzz00000",
            )

    def test_empty_approver_raises(self) -> None:
        """Anti-forgery: approver must be present."""
        with pytest.raises(ToolValidationError, match="approver"):
            record_approval_decision(
                workflow_id="wf_005",
                decision="APPROVED",
                approver="",
                reasoning="",
                correlation_id="appr_aaa00001",
            )

    def test_empty_correlation_id_raises(self) -> None:
        """Anti-forgery: correlation_id must be present."""
        with pytest.raises(ToolValidationError, match="correlation_id"):
            record_approval_decision(
                workflow_id="wf_006",
                decision="APPROVED",
                approver="pd@test-domain.com",
                reasoning="",
                correlation_id="",
            )

    def test_invalid_decision_string_raises(self) -> None:
        with pytest.raises(ToolValidationError, match="Invalid decision"):
            record_approval_decision(
                workflow_id="wf_007",
                decision="MAYBE",
                approver="pd@test-domain.com",
                reasoning="",
                correlation_id="appr_bbb00002",
            )

    def test_pending_decision_raises(self) -> None:
        """PENDING is not a valid decision value for record_approval_decision."""
        with pytest.raises(ToolValidationError, match="PENDING"):
            record_approval_decision(
                workflow_id="wf_008",
                decision="PENDING",
                approver="pd@test-domain.com",
                reasoning="",
                correlation_id="appr_ccc00003",
            )

    def test_empty_workflow_id_raises(self) -> None:
        with pytest.raises(ToolValidationError, match="workflow_id"):
            record_approval_decision(
                workflow_id="",
                decision="APPROVED",
                approver="pd@test-domain.com",
                reasoning="",
                correlation_id="appr_ddd00004",
            )
