# MVP Roadmap — Autonomous SSL Certificate Renewal Agent

> **Versions:** MVP (v1.0) · v1.1 (hardening) · v2.0 (fleet expansion)  
> **Effort estimates** are person-days (pd); see Part V task backlog in `main-prompt.md` for per-task breakdown.  
> **Definition of Done (overall):** all tasks T01–T20 pass acceptance criteria; Part VI Critical Review yields *Approved* or *Approved with conditions*; Appendix A deliverables checklist 100% complete.

---

## Release Summary

| Release | Scope | Target | Total Effort | Status |
|---------|-------|--------|-------------|--------|
| **v1.0 MVP** | Core 6-step workflow + 8 guardrails + fleet-scale batch + audit + Backend event-driven entry | Production-ready | ~55 pd | 🔴 Not started |
| **v1.1** | Magentic retry hardening + Copilot status topic + PromptFlow evals + Direct-mode surfaces | Hardened | ~27 pd | 🔴 Not started |
| **v2.0** | Wildcard CAB flow + sibling agents + multi-CA + auto cert binding | Expanded fleet | ~40+ pd | 🔴 Roadmap |

---

## v1.0 — MVP

**Objective:** A production-grade system that automates the 6-step SSL renewal workflow for single-hostname and multi-SAN (non-wildcard) certs via Client PKI, with fleet-scale batch processing and a tamper-evident audit trail. All 8 guardrails enforced in code. One human gate preserved.

**What it includes:**

### Phase 1 — Business Analysis (this phase)
- `docs/brd.md`, `docs/user-stories.md`, `docs/feature-matrix.md`, `docs/mvp-roadmap.md`
- Stakeholder sign-off from SSL team, PD, PKI, CAB

### Phase 3 — System Architecture
- Component, sequence, and deployment diagrams
- ADR-001 (framework), ADR-002 (model), ADR-003 (hosted vs. external MCP), ADR-004 (Cosmos vs. SQL)
- Batch/fan-out orchestration model
- Three-mode adapter architecture (Direct/Embedded/Backend over one guarded core)

### Phase 4 — Technology Selection
- Version-pinned `pyproject.toml` / `requirements.txt`
- Tech-decision records for all major choices

### Phase 5 — Data Design
- Cosmos DB schemas: `workflow_state`, `audit_log`, `batch`, `idempotency`
- Hash-chain implementation (tamper-resistant audit)
- Blob WORM for CER files (7-year legal hold)

### Phase 6 — Security Engineering
- STRIDE threat model + OWASP/LLM Top-10 mapping
- `PolicyMiddleware` and `AuditMiddleware`
- Identity + least-privilege role assignments
- Prompt Shield + Content Safety integration

### Phase 7 — API & Tool Design
- Native tool contracts: `generate_csr`, `verify_cer`, `request_approval` / `record_approval_decision`
- MCP tool inventory (hosted + external)
- OpenAPI for all 4 Function endpoints

### Phase 8 — Development (Core)
| Deliverable | Location | P0/P1 Tasks |
|------------|----------|------------|
| Config + settings | `src/config.py` | T01 |
| State machine | `src/orchestrator/state_machine.py` | T02 |
| PolicyMiddleware | `src/middleware/policy_middleware.py` | T03 |
| AuditMiddleware | `src/middleware/audit_middleware.py` | T04 |
| `generate_csr` tool | `src/tools/generate_csr.py` | T05 |
| `verify_cer` tool | `src/tools/verify_cer.py` | T06 |
| Approval tools (HITL) | `src/tools/approval_tool.py` | T07 |
| Hybrid MCP assembly | `src/orchestrator/mcp_tools.py` | T08 |
| Orchestrator wiring + prompt | `src/orchestrator/agent.py`, `prompts.py` | T09 |
| Persistence (Cosmos + Blob) | `src/persistence/` | T11 |
| Function endpoints | `src/functions/` | T12 |
| Batch coordinator + rate limiter | `src/orchestrator/batch_coordinator.py`, `rate_limiter.py` | T19 |
| Backend event adapter | `src/interfaces/backend/event_trigger.py` | T20 (partial) |

### Phase 9 — Testing
- Unit, integration, API, and security test suites
- ≥ 80% coverage gate
- 20 E2E synthetic renewals reaching correct terminal states
- All security tests passing (mandatory set P9.5)

### Phase 10 — DevOps
- `deploy.yml` (OIDC federated CI/CD)
- `bob-review.yml` (dev-plane PR gate)
- Branch protection on `main`

### Phase 11 — Cloud Deployment (IaC)
- Complete Bicep module set (`infra/`)
- 8-step rollout order (network → data → AI → APIM → messaging → compute → UX → observability)

### Phase 12 — Observability
- OTel tracing per `workflow_id`
- Azure Workbook renewal funnel
- Alert rules: stuck workflow, verifier failure, Cosmos throttle, PKI overdue, dead-letter > 0

### Phase 13 — Performance
- Idempotency store
- Async I/O throughout
- Load test: 100-cert wave, 0 duplicates, 0 quota breaches

### Phase 14 — Documentation
- `architecture.md`, `developer-guide.md`, `deployment-guide.md`, `RUNBOOK.md`, `dr-guide.md`, `troubleshooting.md`, `compliance.md`, ADRs

### Phase 15 — Go-Live
- Readiness checklists
- Go/No-Go review (criteria in §15.2)
- Canary cutover (1 low-risk hostname → small cohort → full fleet)
- 30-day manual-runbook fallback

**MVP Exit Criteria:**

1. All tasks T01–T19 (P0/P1) pass acceptance criteria and verification commands.
2. All 8 guardrails verified in code (not just tests): G1–G8.
3. ≥ 80% test coverage; all security tests green; 20/20 E2E synthetic renewals reach correct terminal states.
4. `pip-audit` and Dependabot clean.
5. `az deployment group what-if` clean on all Bicep modules.
6. Part VI Critical Review yields **Approved** or **Approved with conditions** (Medium items only).
7. Stakeholder sign-offs: SSL team (Priya), PD (David), PKI (Mei), CAB.
8. Canary renewal completed successfully with full audit trace reconstructable.

---

## v1.1 — Hardening & Direct-Mode Surfaces

**Objective:** Add remaining S-tier features, improve reliability, and deliver operator-facing surfaces.

**Target window:** ~4 weeks after v1.0 goes live in production.

| Feature | Tasks | Effort |
|---------|-------|--------|
| Magentic retry sub-orchestration (full implementation) | T10 | 4 pd |
| Copilot Studio "Check Status" topic (generative status query) | T13 | 2 pd |
| Direct mode: Slack app + slash commands (`/ssl-status`, `/ssl-renew`, `/ssl-batch`) | T20 | 3 pd |
| Direct mode: Web console API (`GET /workflows/{id}`, `/batches/{id}`, `/approvals`) | T20 | 3 pd |
| Batch approval Adaptive Card (per-cert toggles + bulk actions) | T13 | 2 pd |
| PKI reply reminders (24h / 72h) + 48h approval escalation to delegate | T07/P13 | 2 pd |
| PromptFlow nightly evals (groundedness + tool-call accuracy + guardrail adherence) | T15 | 3 pd |
| Power Automate approval fallback (writes same Cosmos record) | T13 | 2 pd |
| Embedded dashboard suggestion service (read-only) | T20 | 3 pd |
| Dev-plane Bob agents + Bobalytics | T18 | 3 pd |
| **Total** | | **~27 pd** |

**v1.1 Exit Criteria:**
- T10, T13, T18, T20 all pass acceptance criteria
- PromptFlow eval job green (groundedness ≥ 0.9, tool-call accuracy ≥ 0.95)
- Slack signature verification test passes (`test_slack_signature_required`)
- All-mode test passing (`test_all_modes_hit_guarded_core`, `test_embedded_is_read_only`)

---

## v2.0 — Fleet Expansion

**Objective:** Extend to wildcard certificates (via CAB), sibling agents, multi-CA routing, and auto cert binding.

**Target window:** Roadmap — timing TBD based on v1.1 outcomes.

| Feature | Notes |
|---------|-------|
| Wildcard certificate CAB path | Separate approval workflow; G6 block lifted for CAB-pre-approved CNs only |
| Code-signing certificate renewal sibling agent | Shares the orchestrator pattern; different tool set |
| mTLS client certificate renewal sibling agent | As above |
| Multi-CA routing | Vendor-specific adapters behind a CA interface |
| Automatic certificate binding to endpoints | Post-install step; requires endpoint inventory + binding tool |
| Self-service portal UI (Carbon design system) | Full web UI for certificate lifecycle management |
| Active-passive DR region failover | Currently mitigated by zone redundancy + manual runbook |

---

## Milestone Timeline (indicative)

```
Month 1:  P1–P4 (BRD, UX, Architecture, Tech Selection) — Docs + Design
Month 2:  P5–P8 (Data, Security, API Design, Core Development) — T01–T12
Month 3:  P8c/8d/8e, P9 (Tools, Batch, Interfaces, Testing) — T13–T20
Month 4:  P10–P11 (DevOps, IaC) — CI/CD + Bicep
Month 5:  P12–P13 (Observability, Performance) — Dashboards + Load tests
Month 6:  P14–P15 (Docs, Go-Live) — Sign-offs + Canary

Quarter 3: v1.1 hardening (Magentic, Slack, PromptFlow evals, Bob agents)
Quarter 4: v2.0 design kick-off (wildcard, sibling agents)
```

---

## Risk-Adjusted Priorities

| Risk | Impact on Roadmap | Mitigation |
|------|------------------|-----------|
| MAF 1.0 GA date drift | Delays P8 implementation | Pin versions; SK fallback within 1-yr support window |
| PKI mailbox SLA unpredictable | v1.0 cycle-time KPI at risk | Reminders + escalation (S-06, S-07) land in v1.1 but wire timer scaffolding in v1.0 |
| Dynatrace alert format varies by environment | Alert parser needs flexible schema | CMDB enrichment is mandatory; fail-to-PD on enrichment gap (AC-1.3) |
| 100-cert wave exceeds Cosmos RU | Performance SLO breach | Autoscale + headroom in Bicep; load test before go-live |
