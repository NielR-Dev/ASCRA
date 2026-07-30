"""Test data factories for ASCRA.

Plain Python factory functions — no external library dependencies.
Each function accepts keyword overrides so tests can vary only the field they care about.

Usage:
    from tests.factories import make_canonical_alert, make_workflow_state, load_fixture

    alert = make_canonical_alert(cn="api.prod.test-domain.com")
    wf = make_workflow_state("APPROVED", cn="api.prod.test-domain.com")
    raw = load_fixture("alerts/dynatrace_ssl_expiry_single.json")
"""
from __future__ import annotations

import json
import pathlib
import uuid
from typing import Any

_FIXTURES_DIR = pathlib.Path(__file__).parent / "fixtures"

_DEFAULT_CN = "api.prod.test-domain.com"
_DEFAULT_SAN = ["api.prod.test-domain.com", "api-internal.prod.test-domain.com"]
_DEFAULT_APP = "Orders-API"
_DEFAULT_APPROVER = "pd@test-domain.com"
_DEFAULT_WF_ID = "wf_2026-07-28_api.prod.test-domain.com_7f3a"
_DEFAULT_BATCH_ID = "batch_2026-07-28_wave_ca-rotation_4a1c"


def load_fixture(relative_path: str) -> Any:
    """Load a JSON fixture from tests/fixtures/<relative_path>."""
    path = _FIXTURES_DIR / relative_path
    return json.loads(path.read_text(encoding="utf-8"))


def make_dynatrace_event(
    cn: str = _DEFAULT_CN,
    san: list[str] | None = None,
    problem_id: str = "P-12345",
    owning_application: str = _DEFAULT_APP,
    wrap_event_grid: bool = False,
    **overrides: Any,
) -> dict[str, Any]:
    """Raw Dynatrace SSL-expiry event, optionally wrapped in an Event Grid envelope."""
    if san is None:
        san = list(_DEFAULT_SAN) if cn == _DEFAULT_CN else [cn]

    payload: dict[str, Any] = {
        "cn": cn,
        "san": san,
        "problemId": problem_id,
        "problemTitle": "SSL Certificate Expiry Alert",
        "severity": "HIGH",
        "owning_application": owning_application,
        "timestamp": "2026-07-28T13:02:11Z",
        **overrides,
    }

    if not wrap_event_grid:
        return payload

    return {
        "id": f"eg-{uuid.uuid4().hex[:8]}",
        "source": "/subscriptions/00000000-0000-0000-0000-000000000000/providers/Microsoft.EventGrid",
        "specversion": "1.0",
        "type": "Dynatrace.Problem.SSLCertificateExpiry",
        "time": "2026-07-28T13:02:11Z",
        "datacontenttype": "application/json",
        "data": payload,
    }


def make_canonical_alert(
    cn: str = _DEFAULT_CN,
    san: list[str] | None = None,
    problem_id: str = "P-12345",
    owning_application: str = _DEFAULT_APP,
    source: str = "dynatrace",
    received_at: str = "2026-07-28T13:02:11Z",
    **overrides: Any,
) -> dict[str, Any]:
    """Normalized canonical alert dict — the shape produced by parse_dynatrace_alert()."""
    if san is None:
        san = list(_DEFAULT_SAN) if cn == _DEFAULT_CN else [cn]

    return {
        "cn": cn,
        "san": san,
        "owning_application": owning_application,
        "source": source,
        "problem_id": problem_id,
        "received_at": received_at,
        **overrides,
    }


def make_workflow_state(
    state: str = "PARSED",
    workflow_id: str | None = None,
    cn: str = _DEFAULT_CN,
    san: list[str] | None = None,
    owning_application: str = _DEFAULT_APP,
    batch_id: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """Complete workflow_state document at the given lifecycle state.

    Populates fields appropriate to that state:
    - CSR_READY and beyond: csr block filled
    - CSR_REQUESTED and beyond: jira_ticket set
    - APPROVED and beyond: approval block filled
    - PKI_REPLIED and beyond: pki block filled, cer_blob_url set
    - VERIFIED and beyond: verification.pass_ = True, checks filled
    - COMPLETE: chg_number set
    - REJECTED: approval.decision = REJECTED
    - FAILED: verification failed, retry exhausted
    """
    if san is None:
        san = list(_DEFAULT_SAN) if cn == _DEFAULT_CN else [cn]
    if workflow_id is None:
        slug = cn.replace(".", "-")
        workflow_id = f"wf_2026-07-28_{slug}_7f3a"

    _LIVE_STATES = {
        "ALERT_RECEIVED", "PARSED", "CSR_READY", "CSR_REQUESTED",
        "APPROVED", "PKI_REPLIED", "VERIFIED", "COMPLETE", "REJECTED", "FAILED",
    }
    _CSR_STATES = {"CSR_READY", "CSR_REQUESTED", "APPROVED", "PKI_REPLIED", "VERIFIED", "COMPLETE", "REJECTED", "FAILED"}
    _JIRA_STATES = {"CSR_REQUESTED", "APPROVED", "PKI_REPLIED", "VERIFIED", "COMPLETE", "REJECTED", "FAILED"}
    _APPROVAL_STATES = {"APPROVED", "PKI_REPLIED", "VERIFIED", "COMPLETE", "REJECTED"}
    _PKI_STATES = {"PKI_REPLIED", "VERIFIED", "COMPLETE", "FAILED"}
    _VERIFIED_STATES = {"VERIFIED", "COMPLETE"}

    csr = None
    if state in _CSR_STATES:
        jira_ticket = "SSL-4821" if state in _JIRA_STATES else None
        csr = {
            "key_vault_key_id": f"https://kv-ssl-hsm.vault.azure.net/certificates/{workflow_id}/ab12ef34",
            "csr_pem_sha256": "a1b2c3d4e5f6" * 5 + "a1b2",
            "jira_ticket": jira_ticket,
            "requested_at": "2026-07-28T13:05:40Z" if jira_ticket else None,
        }

    approval = None
    if state in _APPROVAL_STATES:
        decision = "REJECTED" if state == "REJECTED" else "APPROVED"
        reasoning = (
            "CN does not match CMDB. Update CMDB first."
            if decision == "REJECTED"
            else "Verified CN and SAN match CMDB record. Proceeding."
        )
        approval = {
            "approver": _DEFAULT_APPROVER,
            "decision": decision,
            "reasoning": reasoning,
            "decided_at": "2026-07-28T13:20:03Z",
            "card_correlation_id": "appr_9c2e3f1a",
        }

    pki: dict[str, Any] = {"email_thread_id": None, "sent_at": None, "reply_at": None, "reminders_sent": 0}
    if state in _PKI_STATES:
        pki = {
            "email_thread_id": "AAMkADExampleThreadId001",
            "sent_at": "2026-07-28T13:21:00Z",
            "reply_at": "2026-07-29T09:15:00Z" if state != "FAILED" else "2026-07-29T10:00:00Z",
            "reminders_sent": 0 if state != "FAILED" else 1,
        }

    cer_blob_url = None
    if state in _PKI_STATES:
        cer_blob_url = f"https://sslprodcerarti.blob.core.windows.net/cer-artifacts/{workflow_id}.cer"

    verification_pass = None
    verification_checks: dict[str, bool] = {}
    if state in _VERIFIED_STATES:
        verification_pass = True
        verification_checks = {"cn_match": True, "san_match": True, "not_expired": True, "min_validity": True}
    elif state == "FAILED":
        verification_pass = False
        verification_checks = {"cn_match": True, "san_match": False, "not_expired": True, "min_validity": True}

    chg_number = "CHG0048210" if state == "COMPLETE" else None

    idem_jira = f"idem_jira_{workflow_id}" if state in _JIRA_STATES else None
    idem_email = f"idem_email_{workflow_id}" if state in _PKI_STATES else None
    idem_chg = f"idem_chg_{workflow_id}" if state in {"VERIFIED", "COMPLETE"} else None

    retry_rounds = 6 if state == "FAILED" else 0
    retry_escalations = 2 if state == "FAILED" else 0

    doc: dict[str, Any] = {
        "id": workflow_id,
        "workflow_id": workflow_id,
        "batch_id": batch_id,
        "state": state,
        "cn": cn if state != "ALERT_RECEIVED" else "",
        "san": san if state != "ALERT_RECEIVED" else [],
        "owning_application": owning_application if state != "ALERT_RECEIVED" else "",
        "alert": {
            "source": "dynatrace",
            "problem_id": "P-12345",
            "received_at": "2026-07-28T13:02:11Z",
        },
        "csr": csr,
        "approval": approval,
        "pki": pki,
        "verification": {
            "pass_": verification_pass,
            "checks": verification_checks,
            "cer_blob_url": cer_blob_url,
            "verified_at": "2026-07-29T09:16:00Z" if verification_pass else None,
        },
        "change": {
            "chg_number": chg_number,
            "created_at": "2026-07-29T09:17:30Z" if chg_number else None,
        },
        "retry": {"rounds": retry_rounds, "escalations": retry_escalations},
        "idempotency_keys": {
            "jira_create": idem_jira,
            "email_send": idem_email,
            "chg_create": idem_chg,
        },
        "thread_id": workflow_id,
        "created_at": "2026-07-28T13:02:12Z",
        "updated_at": "2026-07-29T09:17:30Z" if state == "COMPLETE" else "2026-07-28T13:05:00Z",
        "schema_version": 1,
    }
    doc.update(overrides)
    return doc


def make_batch_alerts(
    n: int = 5,
    cn_template: str = "api-{i:04d}.test.test-domain.com",
    problem_id_base: int = 20000,
) -> list[dict[str, Any]]:
    """Return n canonical alert dicts for batch tests."""
    return [
        make_canonical_alert(
            cn=cn_template.format(i=i),
            san=[cn_template.format(i=i)],
            problem_id=f"P-{problem_id_base + i}",
            owning_application=f"App-{i:04d}",
        )
        for i in range(n)
    ]


def make_approval_payload(
    workflow_id: str = _DEFAULT_WF_ID,
    thread_id: str | None = None,
    decision: str = "APPROVED",
    approver: str = _DEFAULT_APPROVER,
    reasoning: str = "Approved for testing.",
    **overrides: Any,
) -> dict[str, Any]:
    """Single-cert POST /api/approval-callback request body."""
    return {
        "thread_id": thread_id or workflow_id,
        "workflow_id": workflow_id,
        "decision": decision,
        "approver": approver,
        "reasoning": reasoning,
        **overrides,
    }


def make_batch_approval_payload(
    batch_id: str = _DEFAULT_BATCH_ID,
    workflow_ids: list[str] | None = None,
    decision: str = "APPROVED",
    approver: str = _DEFAULT_APPROVER,
    **overrides: Any,
) -> dict[str, Any]:
    """Batch POST /api/approval-callback request body."""
    if workflow_ids is None:
        workflow_ids = [_DEFAULT_WF_ID]
    return {
        "batch_id": batch_id,
        "approver": approver,
        "decisions": [
            {"workflow_id": wf_id, "decision": decision, "reasoning": "Approved for testing."}
            for wf_id in workflow_ids
        ],
        **overrides,
    }


def make_pki_reply_payload(
    workflow_id: str = _DEFAULT_WF_ID,
    cer_blob_url: str | None = None,
    **overrides: Any,
) -> dict[str, Any]:
    """POST /api/pki-reply request body."""
    if cer_blob_url is None:
        cer_blob_url = f"https://sslprodcerarti.blob.core.windows.net/cer-artifacts/{workflow_id}.cer"
    return {
        "workflow_id": workflow_id,
        "cer_blob_url": cer_blob_url,
        **overrides,
    }
