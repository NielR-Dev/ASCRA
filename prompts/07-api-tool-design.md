# Phase 7 — API & Tool Design

> **Pre-read:** [00-context.md](00-context.md) · depends on P3, P6
> **Deliverable:** Native tool contracts, MCP inventory, Function endpoint OpenAPI
> **Task IDs:** T05, T06, T07, T08
> **Effort estimate:** ~4–5 person-days

---

## Your Task

Define and implement the full tool surface: native MAF tools (`generate_csr`, `verify_cer`, `request_approval`), MCP tool wrappers, and the four Azure Function HTTP endpoints. Every tool is typed, idempotent, and raises a typed error.

---

## What to Produce

1. **`src/tools/generate_csr.py`** — native `@tool`: Key Vault HSM, non-exportable CSR
2. **`src/tools/verify_cer.py`** — native `@tool`: deterministic X.509 verifier
3. **`src/tools/approval_tool.py`** — native `@tool`: request + record approval decision
4. **`src/orchestrator/mcp_tools.py`** — hybrid MCP assembly (hosted + APIM-fronted)
5. **`src/functions/orchestrate/__init__.py`** — HTTP Function: alert trigger
6. **`src/functions/approval_callback/__init__.py`** — HTTP Function: approval callback
7. **`src/functions/pki_reply/__init__.py`** — HTTP Function: PKI CER callback
8. **`src/functions/status/__init__.py`** — HTTP Function: status query
9. **`docs/openapi.yaml`** — OpenAPI 3.0 spec for all four Function endpoints

---

## Error Taxonomy (apply to ALL tools)

```python
class ToolValidationError(ValueError):
    """Bad args — non-retryable (400)."""

class ToolTransientError(RuntimeError):
    """Transient failure — bounded retry with backoff (429/503)."""

class ToolFatalError(RuntimeError):
    """Unexpected failure — halt + escalate (G3) after max_consecutive_tool_errors (500)."""
```

---

## `generate_csr` — Canonical Implementation

```python
# src/tools/generate_csr.py
"""Native tool: create a NON-EXPORTABLE HSM key + PKCS#10 CSR in Azure Key Vault (G7)."""
from __future__ import annotations
from dataclasses import dataclass

from agent_framework import tool
from src.config import settings


@dataclass
class CsrResult:
    key_vault_key_id: str
    csr_pem: str
    csr_pem_sha256: str


def _reject_wildcard(cn: str, san: list[str]) -> None:
    if cn.startswith("*.") or any(s.startswith("*.") for s in san):
        raise ToolValidationError("Wildcard certificates are not permitted (G6).")


@tool
def generate_csr(cn: str, san: list[str], owning_application: str, workflow_id: str) -> CsrResult:
    """Generate a 2048-bit RSA key in Key Vault (HSM, non-exportable) and a signed CSR.

    The private key never leaves the HSM and is never returned (G7). Idempotent on workflow_id.
    """
    import hashlib, base64, textwrap
    from azure.identity import DefaultAzureCredential
    from azure.keyvault.certificates import (
        CertificateClient, CertificatePolicy, KeyType, WellKnownIssuerNames,
    )

    _reject_wildcard(cn, san)
    cred = DefaultAzureCredential(managed_identity_client_id=settings.azure_client_id or None)
    client = CertificateClient(vault_url=settings.key_vault_uri, credential=cred)

    policy = CertificatePolicy(
        issuer_name=WellKnownIssuerNames.unknown,
        subject=f"CN={cn}",
        san_dns_names=san,
        exportable=False,                              # G7: non-exportable
        key_type=KeyType.rsa_hsm,
        key_size=2048,
        content_type="application/x-pkcs12",
    )
    cert_name = workflow_id.replace(":", "-")
    # Idempotent: if cert already exists in this workflow, get_certificate returns it
    operation = client.begin_create_certificate(certificate_name=cert_name, policy=policy).result()
    csr_der = operation.csr
    b64 = base64.b64encode(csr_der).decode()
    csr_pem = ("-----BEGIN CERTIFICATE REQUEST-----\n"
               + "\n".join(textwrap.wrap(b64, 64))
               + "\n-----END CERTIFICATE REQUEST-----\n")
    return CsrResult(
        key_vault_key_id=f"{settings.key_vault_uri}/certificates/{cert_name}",
        csr_pem=csr_pem,
        csr_pem_sha256=hashlib.sha256(csr_pem.encode()).hexdigest(),
    )
```

---

## `verify_cer` — Canonical Implementation

```python
# src/tools/verify_cer.py
"""Native tool: deterministic X.509 verification. The verdict is code, not model opinion (G2)."""
from __future__ import annotations
import base64, datetime as _dt
from dataclasses import dataclass, field

from agent_framework import tool
from src.config import settings


@dataclass
class VerifyResult:
    pass_: bool
    reason: str = ""
    checks: dict = field(default_factory=dict)


@tool
def verify_cer(cer_bytes_b64: str, expected_cn: str, expected_san: list[str],
               workflow_id: str) -> VerifyResult:
    """Validate a returned certificate against the request. NEVER passes on mismatch (G2).

    Checks: parses as X.509 (PEM or DER); CN matches; SAN set equals expected;
    notAfter - now >= cert_min_valid_days (365).
    """
    from cryptography import x509
    from cryptography.hazmat.backends import default_backend

    raw = base64.b64decode(cer_bytes_b64)
    try:
        cert = x509.load_pem_x509_certificate(raw, default_backend())
    except ValueError:
        cert = x509.load_der_x509_certificate(raw, default_backend())

    checks: dict = {}
    cn_attr = cert.subject.get_attributes_for_oid(x509.NameOID.COMMON_NAME)
    actual_cn = cn_attr[0].value if cn_attr else ""
    checks["cn_match"] = (actual_cn == expected_cn)

    try:
        san_ext = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
        actual_san = set(san_ext.value.get_values_for_type(x509.DNSName))
    except x509.ExtensionNotFound:
        actual_san = set()
    checks["san_match"] = (actual_san == set(expected_san))

    now = _dt.datetime.now(_dt.timezone.utc)
    not_after = cert.not_valid_after_utc
    checks["not_expired"] = (now < not_after)
    checks["min_validity"] = ((not_after - now).days >= settings.cert_min_valid_days)

    ok = all(checks.values())
    reason = "" if ok else "; ".join(k for k, v in checks.items() if not v) + " failed"
    return VerifyResult(pass_=ok, reason=reason, checks=checks)
```

---

## `request_approval` + `record_approval_decision` — Contracts

```python
# src/tools/approval_tool.py — implement these two tools:

@tool
def request_approval(workflow_id: str, cn: str, san: list[str],
                     owning_application: str, jira_ticket: str) -> ApprovalPending:
    """Send the Adaptive Card to the PD and block the workflow (HITL, G1).
    Returns a correlation_id; the workflow waits for record_approval_decision to be called back."""

@tool
def record_approval_decision(workflow_id: str, decision: str, approver: str,
                              reasoning: str, correlation_id: str) -> ApprovalResult:
    """Callback target called when the PD taps Approve/Reject.
    Validates: approver identity (Entra token), correlation_id binding, MFA assurance.
    Writes decision + audit to Cosmos; transitions state to APPROVED or REJECTED.
    Auto-escalates to delegate if called after APPROVAL_TIMEOUT_HOURS (48h) with no decision."""
```

**Key rule (G1):** the state machine must not transition from `CSR_REQUESTED` to `APPROVED` without a recorded `record_approval_decision` call that has a valid Entra identity + correlation binding.

---

## MCP Tool Assembly

```python
# src/orchestrator/mcp_tools.py
from __future__ import annotations
from typing import Any
from src.config import settings


def build_mcp_tools() -> list[Any]:
    """Hosted (Foundry) + External (APIM-fronted) MCP tools.
    All MCP output is treated as UNTRUSTED data (G5).
    """
    from agent_framework import HostedMcpTool, MCPTool

    hosted = [
        HostedMcpTool(name="graph_mail",  url=settings.mcp_graph_mail_url),
        HostedMcpTool(name="servicenow",  url=settings.mcp_servicenow_url),
        HostedMcpTool(name="azure",       url=settings.mcp_azure_url),
    ]
    external = [
        MCPTool(name="dynatrace", url=settings.mcp_dynatrace_url),   # APIM-fronted
        MCPTool(name="jira",      url=settings.mcp_jira_url),        # APIM-fronted
    ]
    return [*hosted, *external]
```

---

## Azure Function Endpoints

### `POST /api/orchestrate`
- Triggered by Logic App after dequeuing an alert from Service Bus
- Body: `{ "alert": { ... } }`
- Returns: `{ "workflow_id": "wf_…", "state": "PARSED" }`
- Auth: Entra Easy Auth / APIM

### `POST /api/approval-callback`
- Called by Copilot Studio or Power Automate after PD decision
- Body: `{ "thread_id": "…", "decision": "APPROVED", "approver": "pd@…", "reasoning": "…" }`
- Returns: `202 Accepted`
- Auth: Entra token; validates `thread_id` binding

### `POST /api/pki-reply`
- Called by Logic App when PKI email reply arrives
- Body: `{ "workflow_id": "…", "cer_blob_url": "…" }`
- Returns: `202 Accepted`

### `GET /api/status`
- Query params: `?cn=api.prod.example.com` or `?workflow_id=wf_…`
- Returns: current state + timeline + deep links
- Auth: Entra

**All endpoints:** structured error envelope `{ "error": { "code": "…", "message": "…", "correlation_id": "…" } }`, versioned `/api/v1/...`, `schema_version` in bodies.

---

## Acceptance Criteria

- `generate_csr`: non-exportable HSM key (`KeyType.rsa_hsm`, `exportable=False`); wildcard rejected before KV call; idempotent (re-call returns existing key/CSR)
- `verify_cer`: fails on CN mismatch, SAN mismatch, expired cert, < 365 days remaining, bad parse; model cannot override the verdict
- `request_approval`: blocks workflow until a valid `record_approval_decision` callback arrives with matching `correlation_id` and verified Entra identity
- All 5 MCP tools (3 hosted + 2 external) instantiated with correct types; external via APIM
- All 4 Function endpoints require Entra auth, return structured error envelope, echo correlation-id
- Invalid args rejected (400) before any side effects occur

---

## Verification

```bash
pytest tests/test_generate_csr.py tests/test_verify_cer.py tests/test_approval.py tests/test_api.py -v
```

Key test cases:
- `test_generate_csr_non_exportable` — asserts `exportable=False` + `rsa_hsm` in the KV policy call
- `test_generate_csr_idempotent` — second call with same `workflow_id` returns existing data
- `test_verify_cer_rejects_cn_mismatch` — assert `pass_=False` when CN differs
- `test_verify_cer_rejects_san_mismatch` — assert `pass_=False` when SAN set differs
- `test_verify_cer_rejects_short_validity` — assert `pass_=False` when remaining < 365 days
- `test_approval_callback_rejects_wrong_thread_id` — correlation mismatch → 403
- Endpoint tests: 401 without auth; 400 on bad body; 200/202 on valid
