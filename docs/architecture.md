# Architecture — Autonomous SSL Certificate Renewal Agent

> **Version:** 1.0  
> **Aligned to:** SSL Renewal Agentic AI Implementation Guide v1.3 (2026-07-28)  
> **Pattern:** Supervisor–Worker (Orchestrator + specialist tools) · Hybrid MCP · HITL only where policy requires  
> **ADRs:** [ADR-001](adr/ADR-001-framework.md) · [ADR-002](adr/ADR-002-model.md) · [ADR-003](adr/ADR-003-mcp-hosting.md) · [ADR-004](adr/ADR-004-state-store.md)

---

## 1. Architectural Style

**Supervisor–Worker agentic pattern.** A single **Orchestrator Agent** (Azure AI Foundry Agent Service, GPT-4o, MAF 1.0) plans the workflow and delegates to specialist worker tools. External SaaS is reached via **MCP tools** (some Foundry-hosted, some APIM-fronted); security-sensitive logic runs as **native MAF tools**. Deterministic integration runs in **Logic Apps + Power Automate**; compute-heavy tools in **Azure Functions**. State and audit persist in **Cosmos DB**.

**The LLM proposes; the state machine disposes.** The orchestrator's reasoning is valuable for the retry/diagnosis branch (FR-10) — but every workflow step transition is gated by the deterministic `PolicyMiddleware` + `state_machine.py`. A model mistake cannot advance a workflow past a prohibited transition.

---

## 2. Component Diagram

```
┌─────────────────────────────────── RUN PLANE (Microsoft) ────────────────────────────────────┐
│                                                                                                │
│  Event & Data Sources            Orchestrator (Supervisor)          Human-in-the-Loop         │
│   Dynatrace SSL alerts   ──────▶  Azure AI Foundry · GPT-4o ◀──── Teams Adaptive Card        │
│   Azure Monitor/Log Analytics    MAF 1.0 · plan/select/state        Copilot Studio approval   │
│   CMDB / Cert Inventory          │  (flat tool registry)            Product Director (PD)     │
│                                  ▼                                                             │
│   ┌──────────────────────────────────────────────────────────────────────────────────────┐   │
│   │  Flat Tool Registry (Orchestrator sees as one surface)                                │   │
│   │  ┌─────────────────┐  ┌──────────────────────┐  ┌────────────────────────────────┐  │   │
│   │  │  Native MAF     │  │ Foundry-Hosted MCP    │  │ External / APIM-fronted MCP    │  │   │
│   │  │  (@tool)        │  │ (HostedMcpTool)       │  │ (MCPTool via APIM)             │  │   │
│   │  │  generate_csr   │  │ graph_mail            │  │ atlassian (Jira)               │  │   │
│   │  │  verify_cer     │  │ servicenow            │  │ dynatrace                      │  │   │
│   │  │  request_approva│  │ azure                 │  │                                │  │   │
│   │  │  record_approva │  │                       │  │                                │  │   │
│   │  └─────────────────┘  └──────────────────────┘  └────────────────────────────────┘  │   │
│   │  PolicyMiddleware (G1,G2,G3,G6) · AuditMiddleware (G4) · [runs before every tool]   │   │
│   └──────────────────────────────────────────────────────────────────────────────────────┘   │
│                                  │                                                             │
│   Microsoft Cloud Platform:                                                                   │
│   Identity: Entra ID + Managed Identity + Key Vault (HSM)                                    │
│   AI Runtime: Azure AI Foundry · Azure OpenAI (GPT-4o) · MAF 1.0 · PromptFlow               │
│   Integration: APIM (MCP mode) · Logic Apps · Power Automate · Service Bus · Event Grid      │
│   Compute+Data: Azure Functions · Cosmos DB · Blob Storage (WORM) · AI Search (optional)    │
│   Observability: App Insights · Log Analytics · Azure Purview · Defender / Sentinel           │
│                                                                                                │
└────────────────────────────────────────────────────────────────────────────────────────────┘
                         ▲ shared MCP fabric (dashed boundary) ▲
┌───────────────────────────── DEV PLANE (IBM Bob) ──────────────────────────────────────────┐
│  Planner · Code-Gen · Security Review · Validation · Modernisation · Bobalytics             │
│  Bob has read-only repo scope; APIM denies run-plane scopes to Bob's app registration       │
└────────────────────────────────────────────────────────────────────────────────────────────┘
```

---

## 3. Hybrid MCP Integration

| Type | Tools | Hosting | Auth | Notes |
|------|-------|---------|------|-------|
| **Foundry-hosted** (`HostedMcpTool`) | `graph_mail`, `servicenow`, `azure` | Inside Azure AI Foundry | Managed Identity | No self-hosted infra; no VNet plumbing |
| **External / APIM-fronted** (`MCPTool`) | `atlassian` (Jira), `dynatrace` | SaaS via Azure API Management | Entra JWT via APIM | JWT validation + throttling + full request/response logging |
| **Native MAF tools** (`@tool`) | `generate_csr`, `verify_cer`, `request_approval`, `record_approval_decision` | In-process | N/A | Handle private keys, HITL, deterministic guardrails — NOT MCP surfaces |

**All MCP output is untrusted data (G5).** The orchestrator treats Jira comments, email bodies, and any MCP text response as data, never as instructions.

---

## 4. End-to-End Sequence (T0–T8)

| T | Phase | Action | State after |
|---|-------|--------|------------|
| T0 | Trigger | Dynatrace SSL-expiring webhook → Azure Event Grid → Service Bus queue | — |
| T1 | Ingestion | Logic App dequeues → `POST /api/orchestrate` → parse alert + CMDB enrich | `PARSED` |
| T2 | CSR | `generate_csr`: RSA-2048 key + CSR in Key Vault (non-exportable); `jira.create_issue`: open ticket, attach CSR, notify SG | `CSR_REQUESTED` |
| T3 | Approval **HITL** | `request_approval`: Adaptive Card 1.5 sent to PD; workflow blocks | `APPROVED` / `REJECTED` |
| T4 | PKI submit | `graph_mail.send`: CSR Request Form emailed to PKI mailbox; Graph subscription watches reply | (APPROVED) |
| T5 | CER retrieve | On PKI reply: Logic App downloads CER attachment → `POST /api/pki-reply` → CER saved to Blob (WORM) | `PKI_REPLIED` |
| T6 | Verify | `verify_cer`: deterministic checks (parse, chain, CN, SAN, expiry ≥ 365 days); pass → `VERIFIED`; fail → magentic retry | `VERIFIED` |
| T7 | Change ticket | `servicenow.create_chg`: Pre-Approved HDC CHG; CER attached; Jira linked; implementer set | (VERIFIED) |
| T8 | Handoff | Completion Adaptive Card posted with links (Jira, PKI thread, CER Blob, CHG); `workflow_state` updated | `COMPLETE` |

**Timeouts:**
- Approval waits ≤ 48 h then auto-escalates to PD's delegate (reminder, not auto-approve)
- PKI reply waits 5 business days; reminders sent at 24 h and 72 h
- All external tool calls: idempotency keys in Cosmos to prevent duplicate Jira/SNOW/email on retry

---

## 5. State Machine

```
ALERT_RECEIVED → PARSED → CSR_READY → CSR_REQUESTED → APPROVED → PKI_REPLIED → VERIFIED → COMPLETE
                                                      ↘ REJECTED (terminal)
any live state ──────────────────────────────────────────────────────────────────▶ FAILED (terminal)
```

- **Transitions are deterministic code** (`state_machine.py`) — the LLM cannot bypass them.
- `FAILED` is reachable from any non-terminal state (kill-switch / escalation / unrecoverable error).
- `COMPLETE`, `REJECTED`, and `FAILED` are terminal — no further transitions possible.
- The state machine forbids `CSR_REQUESTED → APPROVED` without a recorded PD decision (G1).
- The state machine forbids any transition to `VERIFIED` without a `pass=True` verdict from `verify_cer` (G2).

---

## 6. Fleet-Scale Batch Topology

The run plane is a **two-tier orchestration**: a durable **Batch Coordinator** managing many isolated per-certificate child workflows.

```
Expiry wave (Event Grid → Service Bus)         Batch Coordinator (Durable Function)
  N SSL-expiry alerts  ────────────────────▶    • de-dupe by CN (first occurrence wins)
                                                 • create BATCH record in Cosmos (batch_id)
                                                 • fan-out: 1 child workflow per unique CN
                                                 • bounded concurrency: asyncio.Semaphore(MAX_CONCURRENT_RENEWALS)
                                                 • per-downstream rate limiters (token bucket)
                                                 • fan-in: aggregate per-child outcomes
        ┌───────────────┬───────────────┬───────────────────┐
        ▼               ▼               ▼                   ▼
   child wf #1     child wf #2     child wf #3   …     child wf #N
   (each child = full T0–T8 state machine, isolated, idempotent)
        └───────────────┴───────────────┴───────────────────┘
                        ▼ (fan-in)
             BATCH record: aggregate counts by state, failures, retries → dashboards
```

### Concurrency Model

| Dimension | Mechanism | Configured by |
|-----------|-----------|--------------|
| Parallel | `asyncio.Semaphore(MAX_CONCURRENT_RENEWALS)` — default 20 | `MAX_CONCURRENT_RENEWALS` env var |
| Batch | One `batch_id` groups the wave; batch approval option (P2) | Generated by coordinator |
| Sequence | Per-downstream token-bucket `RateLimiter`: PKI ≤ `PKI_RATE_PER_MIN`/min, Jira ≤ `JIRA_RATE_PER_MIN`/min, SNOW ≤ `SNOW_RATE_PER_MIN`/min | Env vars |

### Isolation Rules

- Children share **no** mutable state — each child has its own `workflow_id`, `workflow_state` doc, and `audit_log` partition.
- A child failure is captured as `FAILED` in the batch aggregate; siblings continue unaffected (FR-15).
- Re-running a batch re-attaches to existing children by `workflow_id` (idempotent — no duplicate Jira/SNOW tickets).
- The Durable Function coordinator survives host restart and resumes from the batch record without re-emailing PKI for already-submitted children.
- A single-cert renewal is a **batch of size 1** — one code path, no special-casing.

---

## 7. Interaction Adapter Layer (one guarded core, many front doors)

See full spec in [`interaction-modes.md`](interaction-modes.md).

```
   DIRECT adapters             EMBEDDED adapters              BACKEND adapters
  ┌────────────────┐         ┌───────────────────┐        ┌──────────────────────┐
  │ Copilot/Teams  │         │ Dashboard suggest. │        │ Event Grid webhook   │
  │ Slack bot/cmds │         │ Card nudges        │        │ Programmatic API/MCP │
  │ Web console API│         │ (read + suggest)   │        │ Approval/PKI callback│
  └───────┬────────┘         └─────────┬─────────┘        │ Scheduled scan (cron)│
          │  normalize+authN           │  read/project     └──────────┬───────────┘
          ▼                            ▼                              ▼
     ┌────────────────────────────────────────────────────────────────────────────┐
     │  GUARDED CORE:  Batch Coordinator → child Orchestrator(s)                  │
     │  State Machine · PolicyMiddleware (G1,G2,G3,G6) · AuditMiddleware (G4)     │
     │  Native tools (generate_csr / verify_cer / request_approval) · HITL        │
     └────────────────────────────────────────────────────────────────────────────┘
```

**Rules:** adapters do protocol translation + authN + normalization only. No business logic. No guardrails. No direct state mutation. Contract test `test_adapter_has_no_logic` enforces this.

---

## 8. Run Plane vs Dev Plane Boundary

| Aspect | Run Plane (Microsoft) | Dev Plane (IBM Bob) |
|--------|----------------------|-------------------|
| Purpose | Execute renewals | Build + maintain the system |
| Key Vault / HSM access | Yes (Managed Identity) | **Never** |
| HITL approvals | Fires them | **Never** |
| Certificate minting | Yes | **Never** |
| Repo access | via CI/CD | Read-only PR scope |
| Shared surface | — | **Only** the APIM/MCP fabric |
| APIM enforcement | — | Bob's app registration **denied** run-plane scopes |

The two planes meet **only** at the shared, Entra-brokered MCP fabric. APIM policy blocks Bob from any run-plane operation. `test_bob_denied_run_plane` (P9.5) asserts this.

---

## 9. Deployment View

```
Azure Region (Singapore / Southeast Asia)
│
├── Hub VNet
│   ├── Azure Firewall (FQDN allow-list for SaaS egress: Jira, Dynatrace, PKI SMTP)
│   └── APIM (MCP mode) — fronts external MCPs only; validates Entra JWT
│
└── Spoke VNet
    ├── Azure AI Foundry (agent + hosted MCP servers)
    │   └── Private Endpoint → Foundry
    ├── Azure OpenAI (GPT-4o) — Private Endpoint
    ├── Azure Functions (orchestrate, approval_callback, pki_reply, status)
    │   └── Private Endpoint → Functions
    ├── Logic Apps Standard (alert dequeue, PKI reply handling)
    ├── Azure Key Vault — Managed HSM
    │   └── Private Endpoint → Key Vault
    ├── Azure Cosmos DB (ssl_renewal DB, 3 containers)
    │   └── Private Endpoint → Cosmos; Zone-redundant
    ├── Azure Blob Storage (cer-artifacts WORM)
    │   └── Private Endpoint → Blob; Zone-redundant
    ├── Azure Service Bus (alert queue)
    │   └── Private Endpoint → Service Bus
    └── Azure Application Insights + Log Analytics Workspace
        └── Purview · Defender / Sentinel
```

**Networking rules:**
- All PaaS behind Private Endpoints; no public data-plane endpoints
- Azure Firewall FQDN allow-list for SaaS egress (Jira, Dynatrace, PKI SMTP relay)
- No public inbound except Event Grid webhook endpoint (secured by Event Grid validation + APIM JWT)
- Bob's MCP calls transit APIM; run-plane operations are denied at the APIM policy layer

---

## 10. Single Points of Failure and Mitigations

| SPOF | Impact | Mitigation |
|------|--------|-----------|
| Orchestrator Function App | No new renewals start | Zone-redundant Functions; Service Bus buffers unprocessed alerts; manual runbook fallback (P14/RUNBOOK.md) |
| Azure Key Vault (HSM) | No CSR generation | HSM SKU HA (built-in redundancy); soft-delete + purge protection; PITR of workflow_state |
| PKI mailbox availability | Workflow stalls at T4/T5 | Automated reminders at 24h and 72h; 5-day SLA + PD escalation on breach |
| APIM (external MCP) | Jira / Dynatrace calls fail | Bounded retry with backoff; fail-closed on G3 cap; degrade to manual for those steps with alert to SRE |
| Azure AI Foundry (single region) | Orchestrator cannot plan/retry | Documented DR region (P14 dr-guide.md); zone-redundant within region; manual runbook active for 30 days post-cutover |
| Cosmos DB | State reads/writes fail | Zone-redundant; continuous backup (PITR, 7-day window); autoscale RU |
| Service Bus | Alert loss under flood | Premium tier with geo-DR option; dead-letter monitoring (P12 alert) |

---

## 11. Quality Attributes

| Attribute | Mechanism |
|-----------|-----------|
| **Scalability** | Stateless workers; Service Bus buffers alert bursts; Cosmos autoscale RU; Functions consumption/premium plan |
| **High Availability** | Zone-redundant Cosmos + Storage; Logic Apps Standard; Foundry managed runtime; retry + idempotency for transient faults |
| **Fault Tolerance** | Each worker maps to one step; a failing MCP server degrades one step, not the whole flow; magentic retry + PD escalation contain verifier/PKI faults; kill-switch disables the orchestrator |
| **Security** | HSM non-exportable keys; least-privilege MI; PolicyMiddleware + AuditMiddleware; Prompt Shield; APIM JWT validation; trust boundary (G5) |
| **Observability** | OTel spans per `workflow_id`; audit middleware; App Insights + Log Analytics + Purview; renewal funnel workbook |
| **Maintainability** | Clean Architecture; config-driven; IaC (Bicep); ADRs; ≥ 80% test coverage |

---

## 12. Architecture Decisions

| ADR | Decision | File |
|-----|---------|------|
| ADR-001 | Agent framework: MAF 1.0 | [ADR-001-framework.md](adr/ADR-001-framework.md) |
| ADR-002 | LLM: GPT-4o (`gpt-4o-2024-11-20`) | [ADR-002-model.md](adr/ADR-002-model.md) |
| ADR-003 | MCP hosting: Foundry-hosted + APIM-fronted hybrid | [ADR-003-mcp-hosting.md](adr/ADR-003-mcp-hosting.md) |
| ADR-004 | State store: Azure Cosmos DB (NoSQL) | [ADR-004-state-store.md](adr/ADR-004-state-store.md) |
