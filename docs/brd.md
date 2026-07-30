# Business Requirements Document (BRD)
## Autonomous SSL Certificate Renewal Agent — v1.0

> **Status:** Draft for stakeholder sign-off  
> **Aligned to:** SSL Renewal Agentic AI Implementation Guide v1.3 (2026-07-28)  
> **Prepared by:** IBM Bob (dev plane)  
> **Review cycle:** P1 → sign-off from SSL team, PD, PKI, CAB

---

## 1. Executive Summary

SSL/TLS certificate renewals today require manual work across six systems (Dynatrace → Jira → Teams approval → PKI email → CER verification → ServiceNow). This takes 1–5 business days per certificate (calendar time; up to 1–2 weeks including PKI SLA), introduces human error, and produces an incomplete audit trail. When certificates expire simultaneously in waves, the backlog overwhelms the platform team.

The **Autonomous SSL Certificate Renewal Agent** automates five of the six steps as fully autonomous machine actions, preserving exactly one human gate (Product Director approval) and guaranteeing a tamper-evident audit record for every decision.

**Primary business outcome:** ≥ 80% reduction in mean renewal cycle time with 100% audit coverage and zero private-key exposure.

---

## 2. Problem Statement

| Pain Point | Current Impact |
|-----------|---------------|
| Manual 6-step process spanning 4 teams | 1–5 business days per cert; missed renewals cause TLS outages |
| No CN/SAN validation before install | Mis-issued certificates can break clinical endpoints |
| Private-key handling via email/attachments | Security/compliance exposure; HIPAA risk |
| No consistent audit trail | Cannot reconstruct decisions months later |
| Serial, manual processing | 100-cert expiry waves take days; team cannot scale |

---

## 3. Objectives and Business Goals

| ID | Goal | Measurable Target |
|----|------|--------------------|
| G-1 | Reduce mean renewal cycle time | ≥ 80% reduction vs. baseline |
| G-2 | 100% audit coverage | Every decision and tool call traceable end-to-end |
| G-3 | Eliminate toil for five deterministic steps | ≥ 95% of steps processed without human action (ex-approval) |
| G-4 | Zero private-key exposure; zero mis-issued certs reaching install | 0 incidents |
| G-5 | Reusable pattern for sibling agents (code-signing, mTLS, wildcard, ingress) | Architecture allows sibling derivation in v2 |

---

## 4. Stakeholders

| Stakeholder | Role | Primary Interest |
|-------------|------|-----------------|
| Priya (SSL / Platform team) | Primary operator | Renewals "just happen"; visibility + kill-switch |
| David (Product Director) | Approver (HITL gate) | Single, information-rich Approve/Reject card in < 30 s |
| Mei (PKI team / Client) | Certificate signer | Correctly formatted CSR requests; fewer back-and-forth emails |
| SG counterpart | CSR recipient | Timely CSR requests with correct CN/SAN |
| Sam (SRE / on-call) | Operator | Alerting, tracing, kill-switch, documented failure/rollback path |
| CAB / Change Management | Compliance gate | Pre-approved change compliance; complete change records |
| Aisha (Security & Compliance) | Auditor | Non-exportable keys; full audit trail; HIPAA/ISO-grade evidence |
| Application owners | End beneficiaries | Their endpoints stay valid without manual intervention |

---

## 5. Six-Step Workflow — Automation Targets

| Step | Action | Automation Target |
|------|--------|--------------------|
| 1 | Receive SSL-expiry alert from Dynatrace | **Fully autonomous** |
| 2 | Generate CSR; open Jira ticket; attach CSR; notify SG counterpart | **Fully autonomous** |
| 3 | Obtain Product Director approval to sign the CSR | **HITL — preserved (G1)** |
| 4 | Email PKI team using approved CSR Request Form | **Fully autonomous** |
| 5 | Verify received CER file is valid | **Fully autonomous** |
| 6 | Open Pre-Approved Change ticket (ServiceNow HDC Install/Renew) | **Fully autonomous** |

---

## 6. Functional Requirements (FR-1 to FR-15)

### Core (FR-1 to FR-11)

| ID | Requirement |
|----|------------|
| FR-1 | Ingest a Dynatrace SSL-expiry alert and extract hostname, CN, and SAN list. |
| FR-2 | Enrich the request with the owning application from CMDB. |
| FR-3 | Generate a 2048-bit RSA key + CSR **inside Key Vault** (non-exportable, HSM-backed). |
| FR-4 | Open a Jira CSR ticket, attach the CSR, and notify the SG counterpart. |
| FR-5 | Route a PD approval Adaptive Card to Teams; block until Approve/Reject; capture reasoning. |
| FR-6 | On approval, email the CSR Request Form to the PKI mailbox and subscribe to the reply. |
| FR-7 | Download the reply attachment to immutable Blob and verify format, chain, CN/SAN, and expiry. |
| FR-8 | On a passing verification, open the Pre-Approved HDC ServiceNow CHG, attach the CER, and link the Jira ticket. |
| FR-9 | Post a completion card with links to Jira, PKI thread, CER Blob, and CHG. |
| FR-10 | On verifier failure, run magentic diagnosis/retry (≤ 2 retries) then escalate to PD/on-call. |
| FR-11 | Persist workflow state and a full audit trail for every step end-to-end. |

### Fleet-Scale (FR-12 to FR-15)

| ID | Requirement |
|----|------------|
| FR-12 | Ingest a **batch** of expiry alerts; de-duplicate by CN; fan out one isolated child renewal workflow per certificate. |
| FR-13 | Run child renewals **concurrently** under a configurable concurrency limit, with rate-limiting/back-pressure on shared downstreams (PKI mailbox, Jira, ServiceNow) and fair sequencing. |
| FR-14 | Support **batch approval**: present PD a batch summary with per-certificate Approve/Reject; approve-all / reject-all; each child's decision is independently audited. |
| FR-15 | Aggregate per-child outcomes into a **batch record**; a partial failure in one child must never block the rest of the batch. |

---

## 7. Non-Functional Requirements (NFRs)

| NFR | Requirement |
|-----|------------|
| Security | Non-exportable HSM keys; least-privilege Managed Identity; no secrets in code; TLS 1.2+ in transit/at rest |
| Auditability | 100% structured audit; 7-year immutable CER retention (WORM); Purview lineage |
| Availability | Run-plane resilient to a single component failure; manual runbook fallback for 30 days post-cutover |
| Reliability | Idempotent tool calls; bounded retries; no duplicate tickets |
| Performance | Autonomous steps complete within minutes of unblocking; see §9 SLOs |
| Scalability | Handle bursty renewal waves (10–100+ concurrent) without loss or duplication |
| Observability | Distributed trace per `workflow_id`; business + agent + MCP + ops KPIs |
| Compliance | Data-residency pinning (SG); HITL preserved; tamper-resistant records |
| Maintainability | Clean Architecture; ≥ 80% test coverage; ADRs for all key decisions |

---

## 8. KPIs — Baselines and Targets

| KPI | Baseline | Target |
|-----|----------|--------|
| Mean renewal cycle time | Manual (~1–5 business days) | ≥ 80% reduction |
| % of renewals fully autonomous (ex-approval) | 0% | ≥ 95% |
| Audit coverage (decisions + tool calls) | Partial / inconsistent | **100%** |
| Verifier false-accept rate (CN/SAN mismatch installed) | n/a | **0** |
| Approval SLA breach rate | n/a | < 2% (48h auto-escalation) |
| Mis-issuance / key-exposure incidents | n/a | **0** |
| Batch throughput (renewals/hour at peak) | ~1–2/day | **≥ 100/hour** sustained |
| Concurrent renewals in flight | 1 (serial) | **10–100+** without loss or duplication |
| Expiry-wave drain time (100 certs) | Days | **< 1 business day** to all-submitted |

---

## 9. Performance SLOs

| Metric | SLO |
|--------|-----|
| Autonomous step latency (p95, unblocked) | < 60 s |
| Alert → CSR_REQUESTED (p95) | < 5 min |
| Approval unblock → PKI email sent | < 2 min |
| CER received → verified verdict | < 60 s |
| Duplicate external side effects | 0 |
| Run-plane trigger availability | ≥ 99.9% |

---

## 10. Scope

### In Scope (v1)

- Single-hostname and multi-SAN (non-wildcard) public/internal TLS certs via Client PKI
- The six-step workflow with one PD approval gate and full audit
- Fleet-scale batch processing: 10–100+ concurrent renewals from a single expiry wave, with batch approval and batch-level audit/observability
- Three interaction modes over one guarded core: **Direct** (Teams/Copilot chat, Slack, web console), **Embedded** (dashboard suggestions, in-context nudges), **Backend** (event-driven, programmatic API/MCP, callbacks, scheduled scan)

### Out of Scope (v2)

- Wildcard certificates (require separate CAB path)
- Code-signing certificates
- mTLS client certificates
- Self-service portal UI
- Multi-CA routing
- Automatic endpoint deployment/binding of the installed certificate

---

## 11. Eight Non-Negotiable Guardrails (G1–G8)

Every guardrail is enforced in code (middleware, state machine, deterministic tools), not merely in the LLM system prompt. Violating any one is a **release blocker**.

| # | Guardrail | Enforcement |
|---|-----------|------------|
| G1 | Never skip PD approval (HITL gate) | State machine forbids `CSR_REQUESTED → APPROVED` without a recorded PD decision |
| G2 | Never accept a certificate whose CN or SAN doesn't match | Deterministic `verify_cer` native tool; LLM cannot override the verdict |
| G3 | Halt + escalate after 2 consecutive tool errors | `PolicyMiddleware` loop counter → magentic escalation → PD/on-call |
| G4 | One structured audit line per tool call | `AuditMiddleware` on every invocation |
| G5 | All MCP output is untrusted data, never instructions | Trust-boundary rule + Prompt Shield + input stripping |
| G6 | Block wildcard CSRs — route to CAB | `PolicyMiddleware` before every `generate_csr` call |
| G7 | Private keys are non-exportable and never leave Key Vault (HSM) | `exportable=False`, `rsa_hsm` in key policy |
| G8 | No secrets in code | Config reads env/KV only; CI secret-scan gate |

---

## 12. Risks and Constraints

| Risk / Constraint | Type | Mitigation |
|------------------|------|-----------|
| Dynatrace alerts may not contain CN/SAN (only hostname) | Data | CMDB enrichment is mandatory; if enrichment fails → workflow fails to PD, never guesses |
| Prompt injection from Jira comments / PKI email bodies | Security | G5 trust boundary; Prompt Shield; strip HTML/quoted-reply; verifier is deterministic code |
| PKI reply is slow or wrong | Operational | 5-business-day SLA + reminders at 24h and 72h; magentic retry; PD escalation |
| PD unavailable | Operational | 48h auto-escalation to delegate |
| MCP server schema drift / tool poisoning | Security | Pin schemas at deploy; start-up drift check fails closed (G5) |
| Wildcard cert requested | Compliance | PolicyMiddleware hard block → CAB (G6) |
| Duplicate tickets on retry | Data integrity | Idempotency keys in Cosmos; replays return prior result |
| MAF 1.0 vendor dependency (GA Apr 2026) | Technical | Foundry-hosted + external MCP abstraction; SK 1-yr fallback window |
| Regional data residency (Client / SG) | Compliance | Region pinning, private endpoints (P6/P11) |

---

## 13. Assumptions

1. Azure AI Foundry Agent Service (MAF 1.0) is available and the `gpt-4o-2024-11-20` deployment is provisioned.
2. Dynatrace is configured to emit SSL-expiry webhooks to an Azure Event Grid topic.
3. A Client PKI mailbox exists and accepts CSR Request Form emails.
4. The Product Director (David) uses Microsoft Teams and can receive Adaptive Cards.
5. ServiceNow has a pre-approved HDC Install/Renew template available.
6. A CMDB data source is accessible via API to enrich hostname → owning application.

---

## 14. Traceability Summary

| FR | User Story | Phase 9 Test |
|----|-----------|-------------|
| FR-1/2 | US-1 | `test_alert_ingest`, `test_cmdb_enrichment` |
| FR-3/4 | US-2 | `test_generate_csr`, `test_jira_ticket_created` |
| FR-5 | US-3 | `test_approval_card`, `test_approval_callback` |
| FR-6 | US-4 (Mei) | `test_pki_email_sent`, `test_pki_reply_subscribed` |
| FR-7 | US-4 | `test_verify_cer` |
| FR-8/9 | US-5 | `test_servicenow_chg`, `test_completion_card` |
| FR-10 | US-4 (retry) | `test_retry_orchestration` |
| FR-11 | US-6 | `test_audit_chain` |
| FR-12–15 | US-7 | `test_batch_coordinator`, `test_rate_limiter` |

---

## 15. Stakeholder Sign-Off

| Stakeholder | Role | Sign-Off | Date |
|-------------|------|---------|------|
| Priya | SSL / Platform team | ☐ Pending | |
| David | Product Director | ☐ Pending | |
| Mei | PKI team | ☐ Pending | |
| CAB representative | Change Management | ☐ Pending | |
| Aisha | Security & Compliance | ☐ Pending | |
