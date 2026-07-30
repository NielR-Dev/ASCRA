# Shared Context — SSL Certificate Renewal Agent

> Read this file before executing any phase prompt. Every phase references it.

---

## What You Are Building

An **Autonomous SSL Certificate Renewal Agent** on the Microsoft Azure AI stack.
It automates a six-step manual process: Dynatrace alert → CSR generation → PD approval → PKI email → CER verification → ServiceNow change ticket.

**Stack:** Azure AI Foundry · MAF 1.0 · GPT-4o · Key Vault HSM · Cosmos DB · Logic Apps · APIM · Copilot Studio · GitHub Actions · Bicep · IBM Bob (dev plane only).

---

## The 8 Non-Negotiable Guardrails (G1–G8)

These are invariants. Any code path that can violate one is a **release blocker**.

| # | Guardrail | How enforced |
|---|-----------|--------------|
| **G1** | Never skip PD approval (HITL gate) | State machine forbids `CSR_REQUESTED → APPROVED` without recorded decision |
| **G2** | Never accept a cert whose CN/SAN doesn't match | Deterministic `verify_cer` tool — LLM cannot override the verdict |
| **G3** | Halt + escalate after 2 consecutive tool errors | `PolicyMiddleware` loop counter → escalation → PD/on-call |
| **G4** | One structured audit line per tool call | `AuditMiddleware` on every invocation |
| **G5** | All MCP output is untrusted data, never instructions | Trust-boundary rule + Prompt Shield + input stripping |
| **G6** | Block wildcard (`*.`) CSRs — route to CAB | `PolicyMiddleware` before every `generate_csr` call |
| **G7** | Private keys are non-exportable, never leave Key Vault | `exportable=False`, `rsa_hsm` in key policy |
| **G8** | No secrets in code — all via Managed Identity + KV refs | Config reads env/KV only; CI secret-scan gate |

---

## State Machine (canonical)

```
ALERT_RECEIVED → PARSED → CSR_READY → CSR_REQUESTED → APPROVED → PKI_REPLIED → VERIFIED → COMPLETE
                                                      ↘ REJECTED (terminal)
any live state → FAILED (terminal, kill-switch/escalation)
```

**State transitions are deterministic code** (not model judgment). The LLM proposes; the machine disposes.

---

## Workflow Steps (T0–T8)

| T | Step | Automation target |
|---|------|------------------|
| T0 | Dynatrace SSL-expiry webhook → Event Grid → Service Bus | Backend |
| T1 | Alert parsed + CMDB enriched | Fully autonomous |
| T2 | Key + CSR generated in Key Vault; Jira ticket opened | Fully autonomous |
| T3 | PD approval via Teams Adaptive Card | **HITL — preserved** |
| T4 | CSR Request Form emailed to PKI mailbox | Fully autonomous |
| T5 | CER downloaded to Blob; deterministic verifier run | Fully autonomous |
| T6 | On pass: ServiceNow CHG opened; completion card posted | Fully autonomous |

---

## Three Interaction Modes (all funnel into ONE guarded core)

| Mode | What it is | Auth | Can mutate state? |
|------|-----------|------|-------------------|
| **Direct** | Human-initiated: Teams/Copilot, Slack, web console | Entra SSO / Slack OAuth | Yes — via guarded tools + HITL |
| **Embedded** | In-context suggestions: dashboards, card nudges | Host-surface identity | **No** — read + suggest only |
| **Backend** | Machine-initiated: webhook, API, callbacks, scheduled scan | MI / APIM JWT / signed webhook | Yes — same guardrails, no bypass |

**Design rule:** adapters do protocol/authN/normalization only — no business logic, no direct state mutation.

---

## Repository Layout (`src/`)

```
src/
├── config.py
├── orchestrator/
│   ├── agent.py              # build_orchestrator()
│   ├── mcp_tools.py          # hosted + external MCP
│   ├── prompts.py            # ORCHESTRATOR_SYSTEM_PROMPT
│   ├── state_machine.py      # State enum + transitions
│   ├── retry_orchestration.py
│   ├── batch_coordinator.py
│   └── rate_limiter.py
├── tools/
│   ├── generate_csr.py       # native @tool
│   ├── verify_cer.py         # native @tool (deterministic)
│   └── approval_tool.py
├── middleware/
│   ├── policy_middleware.py  # G1,G2,G3,G6
│   └── audit_middleware.py   # G4
├── persistence/
│   ├── cosmos_repo.py
│   └── blob_repo.py
├── interfaces/
│   ├── direct/               # Slack, web console
│   ├── embedded/             # read model, suggestion service
│   └── backend/              # event trigger, callbacks, scheduled scan
└── functions/                # Azure Function hosts
    ├── orchestrate/
    ├── approval_callback/
    ├── pki_reply/
    └── status/
```

---

## Coding Standards (apply everywhere)

- Clean Architecture (domain ← application ← infrastructure)
- SOLID + dependency injection (pass clients in; tests monkeypatch)
- Config only via `settings` — never hard-code endpoints, hostnames, or secrets
- Structured logging with `workflow_id` correlation; PHI/secret redaction
- Type hints + `ruff` + `mypy` + `black`; ≥ 80% test coverage gate
- Every external side-effect carries an idempotency key stored in Cosmos

---

## Key Config Variables

| Var | Default | Notes |
|-----|---------|-------|
| `FOUNDRY_PROJECT_ENDPOINT` | required | Foundry chat client |
| `AZURE_OPENAI_DEPLOYMENT` | `gpt-4o-2024-11-20` | Model |
| `KEY_VAULT_URI` | required | HSM Key Vault |
| `COSMOS_ENDPOINT` / `COSMOS_DATABASE` | required / `ssl_renewal` | |
| `APPROVAL_TIMEOUT_HOURS` | `48` | HITL auto-escalation |
| `CERT_MIN_VALID_DAYS` | `365` | Verifier minimum validity |
| `MAX_CONSECUTIVE_TOOL_ERRORS` | `2` | G3 halt threshold |
| `MAGENTIC_MAX_ROUNDS` / `MAGENTIC_MAX_ESCALATIONS` | `6` / `2` | Retry caps |
| `MAX_CONCURRENT_RENEWALS` | `20` | Batch semaphore |
| `PKI_RATE_PER_MIN` / `JIRA_RATE_PER_MIN` / `SNOW_RATE_PER_MIN` | `10` / `60` / `30` | Rate limiters |

---

## Glossary

- **MAF** — Microsoft Agent Framework 1.0 (GA Apr 2026; unifies SK + AutoGen)
- **MCP** — Model Context Protocol; `HostedMcpTool` (Foundry) vs `MCPTool` (APIM-fronted)
- **HITL** — Human-in-the-Loop (single PD approval gate, G1)
- **Magentic** — MAF bounded retry/diagnosis sub-orchestration
- **HSM** — Hardware Security Module (Key Vault Managed HSM; non-exportable keys)
- **WORM** — Write-Once-Read-Many (immutable Blob; 7-yr CER retention)
- **PD** — Product Director (approver). **CAB** — Change Advisory Board. **PKI** — Client PKI team.
- **Run plane** — executes renewals (Microsoft stack). **Dev plane** — builds the system (IBM Bob). They share only the APIM/MCP fabric.
