# Feature Prioritization Matrix — MoSCoW
## Autonomous SSL Certificate Renewal Agent — v1.0

> **Framework:** MoSCoW (Must Have / Should Have / Could Have / Won't Have in v1)  
> **Aligned to:** FRs FR-1 to FR-15, guardrails G1–G8, NFRs  
> **Review date:** Phase 1 sign-off

---

## Must Have (Release Blockers — v1 is not shippable without these)

| ID | Feature | Rationale | Enforced by | Mapped FRs |
|----|---------|-----------|------------|-----------|
| M-01 | Six-step renewal happy path (Alert → CSR → Approval → PKI → Verify → CHG) | Core value proposition | State machine + tools | FR-1 to FR-9 |
| M-02 | G1: PD approval gate (HITL) — never auto-approve | Healthcare compliance; legal/regulatory | `request_approval` + state machine | FR-5 |
| M-03 | G2: Deterministic CN/SAN/expiry verifier — `verify_cer` | Prevents mis-issued cert installation | `verify_cer` native tool | FR-7 |
| M-04 | G3: Halt + escalate after 2 consecutive tool errors | Prevents runaway retry loops | `PolicyMiddleware` | FR-10 |
| M-05 | G4: Structured audit line per tool call | 100% audit coverage KPI; compliance | `AuditMiddleware` | FR-11 |
| M-06 | G5: All MCP output is untrusted data (prompt-injection defense) | Primary attack vector; LLM01 | System prompt + Prompt Shield | FR-1/4/6 |
| M-07 | G6: Block wildcard CSRs → CAB route | Security/compliance — wildcard over-scopes trust | `PolicyMiddleware` + `generate_csr` | FR-3 |
| M-08 | G7: Non-exportable HSM key generation | Crown-jewel protection; HIPAA | `generate_csr` `exportable=False` | FR-3 |
| M-09 | G8: No secrets in code; all config from env/KV | Supply-chain security | Config layer; CI secret-scan gate | All |
| M-10 | Full audit trail + hash chain + workflow state persistence (Cosmos) | KPI G-2; Aisha's compliance requirement | `cosmos_repo.py` | FR-11 |
| M-11 | Immutable CER storage (Blob WORM, 7-year legal hold) | Regulatory retention | `blob_repo.py` | FR-7 |
| M-12 | State machine — deterministic transitions only | Prevents LLM from bypassing steps | `state_machine.py` | All |
| M-13 | `PolicyMiddleware` + `AuditMiddleware` wired in front of every tool call | Guardrails applied uniformly | `agent.py` middleware list | G1–G6 |
| M-14 | Idempotency keys for all external side effects (no duplicate Jira/SNOW/email on retry) | Data integrity; F-06 risk | `cosmos_repo.py` idempotency container | FR-4/6/8 |
| M-15 | Fleet-scale batch fan-out/fan-in with bounded concurrency (FR-12, FR-13, FR-15) | Core KPI: ≥ 100 renewals/hour; expiry waves | `batch_coordinator.py` + semaphore | FR-12–15 |
| M-16 | Per-downstream rate limiters (PKI, Jira, ServiceNow) | Prevent quota breaches and mailbox flooding | `rate_limiter.py` | FR-13 |
| M-17 | Backend event-driven entry point (Dynatrace webhook → Event Grid → Service Bus → Orchestrator) | Primary production intake path | `event_trigger.py` + Functions | FR-1/12 |
| M-18 | `POST /api/approval-callback` endpoint — identity + thread_id validated | HITL callback must be forgery-resistant | `approval_callback` Function | FR-5 |
| M-19 | Azure Function hosts for all four API endpoints (`orchestrate`, `approval_callback`, `pki_reply`, `status`) | Production compute layer | `src/functions/` | FR-1/5/7/11 |
| M-20 | ≥ 80% test coverage; all security tests green (P9 mandatory set) | Quality gate | CI coverage gate + pytest | All |

---

## Should Have (High Value — v1 is incomplete but shippable without these)

| ID | Feature | Rationale | Mapped FRs |
|----|---------|-----------|-----------|
| S-01 | Magentic retry sub-orchestration (Diagnostic + Escalation agents) on verifier failure | Reduces manual toil when PKI returns a bad cert | FR-10 |
| S-02 | Copilot Studio "Check Status" topic (natural-language `workflow_id` / CN lookup) | Stakeholder visibility without querying Cosmos directly | FR-11 |
| S-03 | Batch approval Adaptive Card (per-cert toggles + Approve All / Reject All) | David's UX at scale; prevents 100 individual cards | FR-14 |
| S-04 | Direct mode: Slack app + slash commands (`/ssl-status`, `/ssl-renew`, `/ssl-batch`) | Secondary operator surface; useful but not the critical path | T20 |
| S-05 | Direct mode: Web console API (`GET /api/v1/workflows/{id}`, `/batches/{id}`, `/approvals`) | Operator dashboard backend | T20 |
| S-06 | PKI reply reminders at 24h and 72h; 5-day SLA escalation | Reduces PKI delays; improves cycle-time KPI | FR-6 |
| S-07 | 48h auto-escalation to PD delegate on approval timeout | Keeps approval SLA breach rate < 2% | FR-5 |
| S-08 | OpenTelemetry distributed tracing per `workflow_id`; App Insights end-to-end view | Sam's operational observability requirement | NFR Observability |
| S-09 | Azure Workbook — Renewal Funnel dashboard | Priya's visibility requirement | NFR Observability |
| S-10 | Scheduled inventory scan → enqueue expiring certs as batch (proactive backend) | Proactive catch before Dynatrace fires | T20 |
| S-11 | Bicep IaC for full topology (HSM, Cosmos, Blob WORM, APIM, Functions, Logic Apps, network) | Production deployment; without IaC, deployment is manual | P11 |
| S-12 | CI/CD pipeline: `deploy.yml` (OIDC federated) + `bob-review.yml` (dev-plane PR gate) | Automated delivery + quality gate | P10 |

---

## Could Have (Nice to Have — adds value but low risk if deferred to v1.1)

| ID | Feature | Rationale | Candidate release |
|----|---------|-----------|------------------|
| C-01 | Power BI Approvals dashboard (PD decision times, rejection reasons, SLA breach trends) | Insight for PD and management; audit aids | v1.1 |
| C-02 | Embedded dashboard suggestions / proactive nudge cards ("12 certs expire in 30 days") | Priya's proactive awareness; read-only, no risk | v1.1 |
| C-03 | PromptFlow nightly evals + golden dataset (groundedness, tool-call accuracy, guardrail adherence) | Model quality monitoring; important but not Day 1 | v1.1 |
| C-04 | CMDB AI Search grounding for enrichment | Improves enrichment accuracy for complex host mappings | v1.1 |
| C-05 | Issuer / CT-log allow-list in `verify_cer` (defence-in-depth for mis-issuance) | Hardening beyond deterministic CN/SAN check | v1.1 |
| C-06 | Power Automate approval fallback (same Dataverse/Cosmos record as Teams card) | Fallback for PD if Teams is unavailable | v1.1 |
| C-07 | DR region automated failover (active-passive) | Currently mitigated by zone redundancy + manual runbook | v2 |
| C-08 | Self-service portal UI (React / Carbon design system) | Full web UI for operators; lower priority than API | v2 |
| C-09 | Dev-plane Bobalytics dashboard (PR cycle time, defect escape, eval trends) | Dev-plane KPIs; useful but not blocking production | v1.1 |

---

## Won't Have in v1 (Explicitly Out of Scope)

| ID | Feature | Reason for deferral |
|----|---------|---------------------|
| W-01 | Wildcard certificate processing (CAB path) | Requires a separate, more complex CAB approval flow; blocked in v1 by G6 |
| W-02 | Code-signing certificate renewals | Different PKI workflow; separate sibling agent |
| W-03 | mTLS client certificate renewals | Different validation model; separate sibling agent |
| W-04 | Multi-CA routing (multiple PKI providers) | Requires vendor-specific adapters; deferred to v2 |
| W-05 | Automatic certificate binding to endpoints (deploy the installed cert) | Post-install step; out of scope for this agent |
| W-06 | Self-signed or internal CA issuance | Only Client PKI in v1 |
| W-07 | Certificate revocation workflows | Separate lifecycle concern |

---

## Guardrail Coverage in Must Have

All 8 non-negotiable guardrails are covered by Must-Have features. No guardrail is deferred.

| Guardrail | Must-Have Item |
|-----------|---------------|
| G1 (never skip approval) | M-02, M-18 |
| G2 (never accept mismatch) | M-03, M-12 |
| G3 (halt after 2 errors) | M-04, M-13 |
| G4 (audit every call) | M-05, M-13 |
| G5 (MCP output untrusted) | M-06 |
| G6 (block wildcards) | M-07 |
| G7 (non-exportable keys) | M-08 |
| G8 (no secrets in code) | M-09 |
