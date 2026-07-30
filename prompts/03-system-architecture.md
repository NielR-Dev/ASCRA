# Phase 3 — System Architecture

> **Pre-read:** [00-context.md](00-context.md) · depends on P1 output
> **Deliverable:** Architecture diagrams, ADRs, batch/adapter topology specs
> **Effort estimate:** ~5–8 person-days

---

## Your Task

Produce the system architecture: component diagrams, end-to-end sequence, batch topology, adapter layer design, and Architecture Decision Records (ADRs). This is the technical blueprint every downstream phase implements.

---

## What to Produce

1. **`docs/architecture.md`** — component diagram, deployment view, run/dev-plane boundary
2. **`docs/adr/ADR-001-framework.md`** — framework selection (MAF 1.0)
3. **`docs/adr/ADR-002-model.md`** — model selection (GPT-4o)
4. **`docs/adr/ADR-003-mcp-hosting.md`** — hosted vs external MCP
5. **`docs/adr/ADR-004-state-store.md`** — Cosmos vs SQL

---

## Architectural Style

**Supervisor–Worker agentic pattern:**
- One **Orchestrator Agent** (Azure AI Foundry, GPT-4o, MAF 1.0) plans the workflow
- Delegates to specialist worker tools (MCP + native)
- Workers are stateless, unit-testable, and map 1:1 to workflow steps

**Why this pattern:** the Orchestrator centralizes reasoning and can be swapped between Foundry Agent Service (managed) and self-hosted MAF without changing worker code. Workers are reusable for sibling cert workflows.

---

## Hybrid MCP Integration

| Type | Tools | Where |
|------|-------|--------|
| **Foundry-hosted** (`HostedMcpTool`) | `graph_mail`, `servicenow`, `azure` | Inside Azure AI Foundry |
| **External / APIM-fronted** (`MCPTool`) | `atlassian` (Jira), `dynatrace` | SaaS via Azure API Management (MCP mode) |
| **Native MAF tools** (`@tool`) | `generate_csr`, `verify_cer`, `request_approval` | In-process; handle keys, approval, deterministic guardrails |

The Orchestrator sees all three as **one flat tool registry**.

---

## Component Diagram (include in architecture.md)

```
Event & Data Sources           Orchestrator (Supervisor)          Human-in-the-Loop
  Dynatrace SSL alerts   ─────▶  Azure AI Foundry · GPT-4o   ◀────  Teams Adaptive Card
  Azure Monitor/Log Ana.        MAF 1.0 · plan/select/state         Copilot Studio approval
  CMDB / Cert Inventory         │  (flat tool registry)             Product Director (PD)
                                ▼
   Specialist Worker Tools (hybrid):
   [Alert Ingestion·MCP] [Jira CSR·MCP+native] [Approval·native/HITL]
   [PKI Comms·MCP] [Verifier·native] [Change Ticket·MCP]
                                │
   Microsoft Cloud Platform:
   Identity&Secrets(Entra,MI,Key Vault HSM) · AI Runtime(Foundry,AOAI,MAF)
   Integration/MCP Bus(APIM·MCP servers·Logic Apps·Power Automate·Service Bus)
   Compute&Data(Functions·Cosmos·Blob·AI Search) · Observability(App Insights·Purview)
                                ▲ shared MCP (dashed) ▲
   Dev Plane — IBM Bob: Planner · Code-Gen · Security Review · Validation · Bobalytics
```

---

## Sequence — End-to-End Flow (T0–T8)

Document this table in architecture.md:

| T | Phase | Action | State after |
|---|-------|--------|-------------|
| T0 | Trigger | Dynatrace SSL-expiring webhook → Event Grid → Service Bus queue | — |
| T1 | Ingestion | Logic App dequeues → calls Orchestrator → parse alert + CMDB enrich | `PARSED` |
| T2 | CSR | Key+CSR in Key Vault; open Jira; attach CSR; notify SG counterpart | `CSR_REQUESTED` |
| T3 | Approval **HITL** | Adaptive Card to PD; block; approve/reject | `APPROVED` / `REJECTED` |
| T4 | PKI submit | Graph sends email with CSR form; subscription watches reply | (APPROVED) |
| T5 | CER retrieve | On reply, Logic App → download to immutable Blob → run verifier | `PKI_REPLIED` |
| T6 | Verify | Validate format/chain/CN/SAN/expiry; pass → `VERIFIED`; fail → retry/escalate | `VERIFIED` |
| T7 | Change ticket | SNOW CHG (pre-approved), attach CER, set implementer | (VERIFIED) |
| T8 | Handoff | Completion card with links; state = COMPLETE | `COMPLETE` |

**Timeouts:** approval ≤ 48h then auto-escalate; PKI reply waits 5 business days with reminders at 24h and 72h.

---

## Fleet-Scale Batch Topology

The run plane is a **two-tier orchestration**: a **Batch Coordinator** over many **per-certificate child workflows**.

```
Expiry wave (Event Grid → Service Bus)         Batch Coordinator (durable)
  N SSL-expiry alerts  ─────────────────────▶   • de-dupe by CN
                                                 • create BATCH record (batch_id)
                                                 • fan-out: 1 child workflow per cert
                                                 • bounded concurrency (semaphore = MAX_CONCURRENT_RENEWALS)
                                                 • per-downstream rate limiters (PKI / Jira / SNOW)
                                                 • fan-in: aggregate per-child outcomes
        ┌───────────────┬───────────────┬─────────────────────┐
        ▼               ▼               ▼                     ▼
   child wf #1     child wf #2     child wf #3   …       child wf #N
   (each child = full T0–T8, isolated, idempotent)
        └───────────────┴───────────────┴─────────────────────┘
                        ▼ (fan-in)
             BATCH record: counts by state, failures, retries → dashboards + batch audit
```

**Concurrency model:**

| Dimension | Mechanism |
|-----------|-----------|
| Parallel | Bounded async semaphore (`MAX_CONCURRENT_RENEWALS`, default 20) |
| Batch | One `batch_id` groups the wave; batch approval option |
| Sequence | Per-downstream rate limiters (PKI ≤ M emails/min, Jira/SNOW API quotas) + fair queue |

**Design rules:**
- Children share no mutable state; one child failure never aborts siblings (FR-15)
- Re-running a batch re-attaches to existing children (idempotent, no duplicate tickets)
- Coordinator is a **Durable Function** so it survives host restart
- A single renewal is a batch of size 1 — no special-casing

---

## Adapter Layer (one guarded core, many front doors)

```
   DIRECT adapters            EMBEDDED adapters              BACKEND adapters
  ┌────────────────┐        ┌───────────────────┐        ┌──────────────────────┐
  │ Copilot/Teams  │        │ Dashboard suggest. │        │ Event Grid webhook   │
  │ Slack bot/cmds │        │ Card nudges        │        │ Programmatic API/MCP │
  │ Web console API│        │ (read + suggest)   │        │ Approval/PKI callback│
  └───────┬────────┘        └─────────┬─────────┘        │ Scheduled scan (cron)│
          │                           │                  └──────────┬───────────┘
          │  normalize + authN        │  read/project + suggest     │  authN (MI/JWT/sig)
          ▼                           ▼                             ▼
     ┌───────────────────────────────────────────────────────────────────────┐
     │  GUARDED CORE:  Batch Coordinator → child Orchestrator(s)             │
     │  State Machine · PolicyMiddleware · AuditMiddleware · HITL             │
     └───────────────────────────────────────────────────────────────────────┘
```

Adapters do **only** protocol translation + authentication + input normalization. They hold no business rules, no guardrails, no ability to mutate state except via the guarded tools.

---

## Deployment View

- Hub-and-spoke VNet
- All PaaS behind Private Endpoints
- APIM (MCP mode) fronts external MCPs only
- Azure Firewall FQDN allow-list for SaaS egress
- Foundry agent + Functions + Logic Apps + Cosmos + Key Vault (HSM) + Blob (WORM) in the spoke

---

## ADR Contents (minimal required per ADR)

Each ADR file must contain:
- **Context:** what decision was needed
- **Decision:** what was chosen
- **Alternatives considered:** at least one rejected alternative with rationale
- **Consequences:** trade-offs, lock-in, mitigation

---

## Single Points of Failure & Mitigations (include in architecture.md)

| SPOF | Impact | Mitigation |
|------|--------|-----------|
| Orchestrator Function App | No new renewals | Zone redundancy; queue buffers alerts; manual runbook |
| Key Vault (HSM) | No CSR generation | HSM SKU HA; soft-delete + purge protection |
| PKI mailbox | Stalled at T4/T5 | Reminders + 5-day SLA + PD escalation |
| APIM (external MCP) | Jira/Dynatrace calls fail | Retry; fail-closed; degrade to manual for those steps |
| Single Foundry region | Regional outage | Documented DR region (P14) |

---

## Acceptance Criteria

- Every T0–T8 step maps to exactly one worker
- Each SPOF has a documented mitigation
- Architecture supports N concurrent renewals without shared mutable state
- ADRs cover all four decisions (framework, model, MCP hosting, state store)
- Adapter layer diagram shows all three modes converging on one guarded core

---

## Verification

- Architecture review sign-off
- A design walkthrough traces one renewal AND a 50-cert batch end-to-end
- ADRs are merged into `docs/adr/` before Phase 8 begins
- `test_all_modes_hit_guarded_core` (Phase 9) validates the adapter design
