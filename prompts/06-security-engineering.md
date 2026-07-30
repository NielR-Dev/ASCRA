# Phase 6 — Security Engineering

> **Pre-read:** [00-context.md](00-context.md) · depends on P3, P4, P5
> **Deliverable:** PolicyMiddleware, AuditMiddleware, trust-boundary controls, security test suite
> **Task IDs:** T03, T04, T14
> **Effort estimate:** ~4 person-days

---

## Your Task

Implement the security controls: `PolicyMiddleware`, `AuditMiddleware`, the start-up schema-drift check, the kill-switch, the Prompt Shield wiring, and all security-relevant configuration. These are the enforcement layer for guardrails G1–G8.

---

## What to Produce

1. **`src/middleware/policy_middleware.py`** — G1, G2, G3, G6 enforcement
2. **`src/middleware/audit_middleware.py`** — G4 enforcement
3. **`src/config.py`** — includes kill-switch flag + security-relevant settings
4. **Security controls** documented in `docs/security.md` (STRIDE, OWASP, trust boundary)
5. **`tests/test_policy_middleware.py`**, **`tests/test_audit_middleware.py`**, **`tests/test_security.py`**

---

## STRIDE Threat Model

Document in `docs/security.md`:

| Threat | Vector | Control |
|--------|--------|---------|
| **Spoofing** | Fake Dynatrace webhook / forged approval callback | Event Grid + APIM JWT validation; signed webhooks; approval callback verifies Entra token + `thread_id` binding |
| **Tampering** | Altered CSR/CER, mutated audit | HSM signing; Blob WORM; audit hash chain (P5); Cosmos PITR |
| **Repudiation** | "I didn't approve" | Approval captured with Entra identity + MFA + reasoning + correlation id (G4) |
| **Information disclosure** | PHI/secret leakage via logs/errors | Data minimization (P5); no secrets in code (G8); redaction filter; short-TTL SAS on CER links |
| **DoS** | Alert flood, retry storm | Service Bus buffering; magentic round cap + escalation cap; APIM throttling |
| **Elevation of privilege** | Bob or a worker escalates to KV/HITL | Least-privilege MI; Bob denied at APIM; native tools gate sensitive ops |

---

## Prompt Injection Defense (OWASP LLM01 — primary risk)

| LLM Risk | Control |
|----------|---------|
| **LLM01 Prompt Injection** | Treat ALL MCP/tool output as untrusted data (G5). Strip HTML/quoted-reply/signature blocks before the model sees email. Verifier is deterministic code — model cannot override verdict. Azure Prompt Shield + Content Safety on inbound free-text. |
| **LLM02 Insecure Output Handling** | Orchestrator output cannot execute side effects directly — only whitelisted tools with typed args; PolicyMiddleware validates args |
| **LLM04 Model DoS** | Round/escalation caps; token budget; APIM throttle |
| **LLM05 Supply chain** | Pin MCP schemas + package versions; start-up schema-drift check **fails closed** |
| **LLM06 Sensitive info disclosure** | System prompt carries no secrets; data minimization; redacted logs |
| **LLM07 Insecure plugin/tool design** | Native tools have typed contracts, arg validation, idempotency |
| **LLM08 Excessive agency** | HITL gate (G1) before any irreversible external act; wildcard block; kill-switch |
| **LLM09 Overreliance** | Deterministic verifier + PD sees CN/SAN; magentic diagnosis is advisory |
| **LLM10 Model theft** | Managed model in Foundry; no weights exposed |

---

## `PolicyMiddleware` — Canonical Implementation

```python
# src/middleware/policy_middleware.py
"""Hard guardrails enforced BEFORE any tool executes (G1, G2, G3, G6).

The LLM cannot bypass these: middleware runs deterministically around every tool call.
"""
from __future__ import annotations
from typing import Any, Awaitable, Callable

from src.config import settings


class PolicyViolation(RuntimeError):
    """Raised when a tool call violates a non-negotiable guardrail."""


def _is_wildcard(value: str) -> bool:
    return value.strip().startswith("*.") or value.strip() == "*"


class PolicyMiddleware:
    """MAF function middleware: validate args, block wildcards, bound consecutive errors."""

    def __init__(self) -> None:
        self._consecutive_errors = 0

    async def __call__(self, context: Any, next: Callable[[Any], Awaitable[None]]) -> None:
        args = getattr(context, "arguments", {}) or {}

        # G6 — never generate a wildcard CSR.
        if context.function.name == "generate_csr":
            cn = str(args.get("cn", ""))
            san = [str(s) for s in args.get("san", [])]
            if _is_wildcard(cn) or any(_is_wildcard(s) for s in san):
                raise PolicyViolation("Wildcard certificates are not permitted; route to CAB.")

        # G3 — halt + escalate after N consecutive tool errors.
        try:
            await next(context)
            self._consecutive_errors = 0
        except Exception:
            self._consecutive_errors += 1
            if self._consecutive_errors >= settings.max_consecutive_tool_errors:
                raise PolicyViolation(
                    f"Halting after {self._consecutive_errors} consecutive tool errors; escalate to PD."
                )
            raise
```

---

## `AuditMiddleware` — Canonical Implementation

```python
# src/middleware/audit_middleware.py
"""One structured, tamper-evident audit line per tool call (guardrail G4)."""
from __future__ import annotations
import json
import logging
from typing import Any, Awaitable, Callable

logger = logging.getLogger("ssl_renewal.audit")


def _summarize(value: Any, limit: int = 256) -> Any:
    """Redact/trim: never log secrets, private keys, or full cert bytes."""
    text = json.dumps(value, default=str)[:limit]
    return text


class AuditMiddleware:
    """Emit an audit record before and after each tool call."""

    async def __call__(self, context: Any, next: Callable[[Any], Awaitable[None]]) -> None:
        tool = context.function.name
        before = {"event": "tool_call.start", "tool": tool,
                  "input": _summarize(getattr(context, "arguments", {}))}
        logger.info(json.dumps(before))
        try:
            await next(context)
            after = {"event": "tool_call.end", "tool": tool, "status": "ok",
                     "output": _summarize(getattr(context, "result", None))}
            logger.info(json.dumps(after))
        except Exception as exc:
            logger.info(json.dumps({"event": "tool_call.end", "tool": tool,
                                    "status": "error", "error": type(exc).__name__}))
            raise
```

---

## Schema-Drift Check (G5)

Implement as a start-up hook called before the orchestrator handles any requests:

```python
# src/orchestrator/drift_check.py
"""Fails closed if any pinned MCP tool schema has changed since deployment."""
import hashlib, json
from src.config import settings

PINNED_SCHEMAS: dict[str, str] = {
    # tool_name: sha256_of_pinned_schema_json
    # Populate at deploy time from the actual MCP server schemas
}

def check_mcp_schema_drift(tool_name: str, live_schema: dict) -> None:
    """Raise RuntimeError if the live schema hash differs from the pinned hash."""
    expected = PINNED_SCHEMAS.get(tool_name)
    if expected is None:
        return  # Not a pinned tool — skip
    live_hash = hashlib.sha256(json.dumps(live_schema, sort_keys=True).encode()).hexdigest()
    if live_hash != expected:
        raise RuntimeError(
            f"MCP schema drift detected for '{tool_name}'. "
            "Update PINNED_SCHEMAS or investigate potential tool poisoning. Refusing to start."
        )
```

---

## Kill-Switch

Add to `src/config.py`:

```python
# Kill-switch: set AGENT_ENABLED=false to disable all new workflow initiations
agent_enabled: bool = True  # reads from env AGENT_ENABLED
```

Add a check at the top of the orchestrator's trigger function:
```python
if not settings.agent_enabled:
    return func.HttpResponse('{"error":{"code":"agent_disabled"}}', status_code=503)
```

Document the kill-switch procedure in `RUNBOOK.md`.

---

## Trust Boundary Diagram (include in docs/security.md)

```
[UNTRUSTED]  Dynatrace payload · Jira comments · PKI email bodies · any MCP text
     │  (sanitize · strip HTML/quotes · Prompt Shield · treat as data — G5)
     ▼
[TRUSTED CORE]  Orchestrator + native tools + PolicyMiddleware + deterministic verifier
     │  (typed tool args · state machine · HSM · MI)
     ▼
[SIDE EFFECTS] Jira · Email · Blob · ServiceNow · Key Vault  (idempotent · least-priv)
```

---

## Identity & Least-Privilege

Document in `docs/security.md` and implement in Bicep role assignments:

| Component | Identity | Permissions |
|-----------|----------|-------------|
| Orchestrator Function App | Managed Identity | Key Vault `Key Sign` + `Certificate Create` (not Export); Cosmos data-plane RBAC; Blob `Storage Blob Data Contributor` on `cer-artifacts`; Service Bus sender/receiver |
| Logic Apps | Managed Identity | Service Bus receiver; Blob read |
| Graph scopes | App registration | `Mail.Send`, `Mail.Read.Shared` — nothing broader |
| **Bob (dev plane)** | Separate app registration | **Denied all run-plane APIM scopes** |

---

## Acceptance Criteria

- Wildcard CSR blocked at middleware before any side effect
- Injection string in a Jira comment does not alter tool selection or skip approval
- Two consecutive tool errors halt + escalate (does not keep retrying)
- Every tool call produces exactly one start + one end audit record
- No private key, CSR body, or CER bytes appear in any log line
- Schema-drift check fails closed (refuses to start) if a pinned schema hash mismatches
- Kill-switch env var disables new workflow initiations without crashing

---

## Verification (security test suite — all must pass)

```bash
pytest tests/test_security.py tests/test_policy_middleware.py tests/test_audit_middleware.py -v
```

| Test | Guardrail |
|------|-----------|
| `test_wildcard_blocked` | G6 |
| `test_prompt_injection_ignored` | G5, LLM01 |
| `test_cn_mismatch_never_installs` | G2 |
| `test_key_non_exportable` | G7 |
| `test_no_secrets_in_logs` | G8 |
| `test_consecutive_errors_halt` | G3 |
| `test_audit_line_per_call` | G4 |
| `test_bob_denied_run_plane` | Part IV |
| `test_all_modes_hit_guarded_core` | §3.12 |
| `test_embedded_is_read_only` | §2.1b |
| `test_slack_signature_required` | §6.4 |
| `test_adapter_has_no_logic` | §3.12 |
