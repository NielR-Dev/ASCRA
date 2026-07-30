# Phase 1 — Business Analysis

> **Pre-read:** [00-context.md](00-context.md)
> **Deliverable:** Business Requirements Document (BRD) + supporting artefacts
> **Effort estimate:** ~3–5 person-days

---

## Your Task

Produce the BRD and supporting analysis documents that define *what* the system must do and *why*. This is the requirements foundation for every downstream phase.

---

## What to Produce (deliverables)

Create these files (in `docs/` or a `brd/` folder):

1. **`brd.md`** — Full Business Requirements Document
2. **`user-stories.md`** — Representative user stories with acceptance criteria
3. **`feature-matrix.md`** — MoSCoW prioritization
4. **`mvp-roadmap.md`** — MVP vs v1.1 vs v2 scope

---

## Context You Need

### The Problem

Today, SSL/TLS certificate renewal is a **six-step manual process** spanning Dynatrace alerts → Jira CSR requests → Product-Director approval → PKI email exchange → format verification → ServiceNow change ticket. Manual process causes:
- Production TLS outages from missed renewals
- Mis-issued certificates from typos in CN/SAN
- Key exposure via email attachments

### Six Steps & Automation Targets

| Step | Action | Target |
|------|--------|--------|
| 1 | Receive SSL-expiry alert from Dynatrace / SSL team | **Fully autonomous** |
| 2 | Request CSR via Jira (CN/SAN → SG counterpart) | **Fully autonomous** |
| 3 | Obtain Product-Director (PD) approval to sign the CSR | **HITL — preserved** |
| 4 | Email PKI team using approved CSR Request Form | **Fully autonomous** |
| 5 | Verify received CER file is valid | **Fully autonomous** |
| 6 | Open Pre-Approved Change ticket (ServiceNow HDC Install/Renew) | **Fully autonomous** |

### Stakeholders

| Stakeholder | Interest |
|-------------|----------|
| SSL / Platform team (Priya) | Renewals happen automatically; needs visibility + kill-switch |
| Product Director / David | Retains sign-off authority; wants a single clear Approve/Reject card |
| PKI team / Mei | Correctly formatted CSR requests; fewer back-and-forth emails |
| SG counterpart | Timely CSR requests with correct CN/SAN |
| SRE / Sam | Alerting, tracing, documented failure/rollback path |
| CAB / Change management | Pre-approved change compliance; complete change records |
| Security & Compliance / Aisha | Non-exportable keys, full audit trail |
| Application owners | Their endpoints stay valid without manual intervention |

### Business Goals

- **G-1** Reduce mean renewal cycle time by **≥ 80%**
- **G-2** **100% audit coverage** — every decision and tool call traceable end-to-end
- **G-3** Eliminate human toil for five deterministic steps; keep the one policy gate
- **G-4** Zero private-key exposure; zero mis-issued certificates reaching install
- **G-5** Reusable pattern for sibling agents (code-signing, mTLS, wildcard, ingress)

### KPIs (include in BRD)

| KPI | Target |
|-----|--------|
| Mean renewal cycle time | ≥ 80% reduction |
| % renewals fully autonomous (ex-approval) | ≥ 95% |
| Audit coverage | 100% |
| Verifier false-accept rate | 0 |
| Approval SLA breach rate | < 2% (48h auto-escalation) |
| Mis-issuance / key-exposure incidents | 0 |
| Batch throughput | ≥ 100 renewals/hour sustained |
| Concurrent renewals in flight | 10–100+ without loss/duplication |
| Expiry-wave drain time (100 certs) | < 1 business day to all-submitted |

### Fleet-Scale Requirement

The system is a **batch/fleet orchestrator**, not a single-cert tool. Certificates expire in waves (CA policy change, annual cohort, monitoring backfill). The system must:
- Ingest many alerts, fan out one **isolated child workflow per certificate**
- Run them **concurrently** under a bounded limiter
- **Rate-limit** shared downstreams (PKI mailbox, Jira, ServiceNow)
- Support **batch approval** for the PD
- Roll up results into a **batch record** for audit

A single-certificate renewal is a **batch of size 1** — one code path.

### Functional Requirements (FR-1 through FR-15)

Include all 15 FRs in the BRD:

**Core (FR-1 to FR-11):**
- FR-1: Ingest Dynatrace SSL-expiry alert; extract hostname, CN, SAN list
- FR-2: Enrich request with owning application from CMDB
- FR-3: Generate 2048-bit RSA key + CSR inside Key Vault (non-exportable)
- FR-4: Open Jira CSR ticket; attach CSR; notify SG counterpart
- FR-5: Route PD approval Adaptive Card to Teams; block until decision; capture reasoning
- FR-6: On approval, email CSR Request Form to PKI mailbox; subscribe to reply
- FR-7: Download reply attachment to immutable Blob; verify format/chain/CN/SAN/expiry
- FR-8: On pass, open Pre-Approved HDC ServiceNow CHG; attach CER; link Jira ticket
- FR-9: Post completion card with links (Jira, PKI thread, CER Blob, CHG)
- FR-10: On verifier failure, run magentic diagnosis/retry (≤ 2 retries) then escalate
- FR-11: Persist workflow state and full audit trail for every step

**Fleet-scale (FR-12 to FR-15):**
- FR-12: Ingest a batch of expiry alerts; de-duplicate by CN; fan out one isolated child workflow per cert
- FR-13: Run children concurrently under configurable concurrency limit with rate-limiting/back-pressure and fair sequencing
- FR-14: Support batch approval (PD sees batch summary; per-cert approve/reject; batch reasoning captured)
- FR-15: Aggregate per-child outcomes into a batch record; a partial failure never blocks sibling children

### Non-Functional Requirements

| NFR | Requirement |
|-----|-------------|
| Security | Non-exportable HSM keys; least-privilege MI; no secrets in code; TLS in transit/at rest |
| Auditability | 100% structured audit; 7-year immutable CER retention; Purview lineage |
| Availability | Resilient to a single component failure; manual runbook fallback (30 days post-cutover) |
| Reliability | Idempotent tool calls; bounded retries; no duplicate tickets |
| Performance | Autonomous steps complete within minutes of unblocking (see P13 SLOs) |
| Scalability | Handle bursty expiry waves without loss |
| Compliance | Data residency pinning; HITL preserved; tamper-resistant records |
| Maintainability | Clean Architecture; ≥ 80% test coverage; ADRs for key decisions |

### Scope

**In scope (v1):**
- Single-hostname and multi-SAN (non-wildcard) public/internal TLS certs via Client PKI
- Six-step workflow; one PD approval gate; full audit; fleet-scale batch (10–100+ concurrent)
- Three interaction modes: Direct (Teams/Slack/web console), Embedded (dashboard suggestions), Backend (event-driven/API/callbacks/scheduled scan)

**Out of scope (v2):**
- Wildcard certs (CAB path), code-signing certs, mTLS client certs, self-service portal UI, multi-CA routing, automatic cert binding to endpoints

### Risks & Constraints

| Risk | Mitigation |
|------|-----------|
| Prompt injection from Jira/PKI email bodies | Deterministic verifier + HITL + whitelisted tools (G5) |
| PKI reply is slow or wrong | 5-business-day wait + reminders; magentic retry; escalation |
| PD unavailable | 48h auto-escalation to delegate |
| Wildcard cert requested | PolicyMiddleware hard block → CAB (G6) |
| Duplicate tickets on retry | Idempotency keys in Cosmos |
| MAF 1.0 vendor dependency | Foundry-hosted + external MCP abstraction; SK fallback window |

---

## Acceptance Criteria

- Every FR (FR-1 to FR-15) maps to at least one user story with testable acceptance criteria
- Each of the six manual steps has an automation target (5 autonomous + 1 HITL)
- All KPIs have baselines and targets documented
- MoSCoW matrix covers all features; Must-have items include the 8 guardrails
- Scope clearly defines in vs. out of scope for v1

---

## Verification

- Stakeholder sign-off recorded (SSL team, PD, PKI, CAB)
- Traceability table links: FR → User Story → Phase 9 test
- No FR is missing an acceptance criterion
- User stories cover all five personas (Priya, David, Mei, Sam, Aisha)
