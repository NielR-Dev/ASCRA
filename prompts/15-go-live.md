# Phase 15 — Go-Live Readiness

> **Pre-read:** [00-context.md](00-context.md) · depends on all phases
> **Deliverable:** Readiness checklists, Go/No-Go decision, canary cutover plan
> **Task IDs:** T17
> **Effort estimate:** ~2 person-days

---

## Your Task

Complete the go-live readiness review. This is not just a checklist — every item must have evidence. A "yes" without evidence is a "no."

---

## What to Produce

1. **`docs/go-live-evidence-pack.md`** — evidence for every checklist item below
2. **`docs/cutover-plan.md`** — canary → limited cohort → full fleet
3. Go/No-Go decision record (sign-off from PD, SSL team, PKI, CAB, Security)

---

## Readiness Checklists

### Security Checklist

| Item | Evidence required | Status |
|------|------------------|--------|
| G1–G8 guardrail tests pass | `pytest tests/test_security.py` output | ☐ |
| OWASP Top 10 controls in place | `docs/security.md` OWASP table complete | ☐ |
| LLM Top 10 controls in place | `docs/security.md` LLM table complete | ☐ |
| `pip-audit` clean | CI `pip-audit` output shows no CVEs | ☐ |
| Pen-test scheduled | Booked date: ________ | ☐ |
| Bob denied run-plane at APIM | `test_bob_denied_run_plane` green | ☐ |
| MCP schema-drift check live | Start-up log shows drift check run | ☐ |
| Kill-switch tested in UAT | Runbook procedure executed + 503 confirmed | ☐ |

### Architecture Checklist

| Item | Evidence required | Status |
|------|------------------|--------|
| All SPOFs have mitigations | SPOF table in `architecture.md` complete | ☐ |
| State machine is authoritative | No code path bypasses `assert_transition()` | ☐ |
| Hybrid MCP drift check live | UAT start-up log | ☐ |
| Private endpoints only (no public data-plane) | `az keyvault show --query properties.publicNetworkAccess` = `Disabled` | ☐ |

### Performance Checklist

| Item | Evidence required | Status |
|------|------------------|--------|
| SLOs met under load | `tests/test_performance.py` output | ☐ |
| Zero duplicate side effects in load test | Load test assertion log | ☐ |
| Cold-start measured and acceptable (< 10s) | App Insights cold-start metric | ☐ |
| Rate limiters prevent quota breaches | Load test: 0 429s on PKI/Jira/SNOW | ☐ |

### Compliance Checklist

| Item | Evidence required | Status |
|------|------------------|--------|
| Audit hash chain verifiable | Reconstruction query output for a test `workflow_id` | ☐ |
| 7-year WORM retention configured | Blob immutability policy screenshot + CLI output | ☐ |
| HITL gate preserved | E2E test shows PD approval is required for every renewal | ☐ |
| Data residency pinned | Bicep `location` parameter + Cosmos region confirmed | ☐ |
| Purview lineage enabled | Purview scan output showing CER lineage | ☐ |

### Operational Checklist

| Item | Evidence required | Status |
|------|------------------|--------|
| All dashboards live | App Insights workbook + Power BI report URLs | ☐ |
| All alert rules configured | `az monitor metrics alert list` output | ☐ |
| RUNBOOK complete + tested | `docs/RUNBOOK.md` kill-switch procedure tested | ☐ |
| DR guide complete | `docs/dr-guide.md` PITR restore rehearsed | ☐ |
| On-call briefed | Briefing notes on file | ☐ |
| 30-day manual fallback available | `docs/RUNBOOK.md` manual renewal steps complete | ☐ |

### Deployment Checklist

| Item | Evidence required | Status |
|------|------------------|--------|
| Bicep what-if clean (no unexpected changes) | `az deployment ... what-if` output | ☐ |
| Rollback plan documented | `docs/cutover-plan.md` rollback section | ☐ |
| Feature flags (phased enablement) in place | `AGENT_ENABLED=false` at deploy time | ☐ |
| E2E synthetic 20/20 correct terminal states | `pytest tests/test_e2e/` output | ☐ |
| PromptFlow evals ≥ 0.90 threshold | Eval job output | ☐ |

---

## Go/No-Go Decision Framework

**GO** — all conditions met:
- All guardrail + security tests pass
- E2E 20/20 correct terminal states
- PromptFlow evals ≥ 0.90 threshold
- All alerts + kill-switch proven in UAT
- DR restore rehearsed
- Sign-off obtained from: PD + PKI + CAB + SSL team + Security

**APPROVED WITH CONDITIONS** — acceptable with time-boxed remediation for open Medium items only (e.g. DR secondary region not yet set up). Document conditions + remediation deadline.

**REQUIRES REMEDIATION** — any open High risk (F-05/06/07/08/10 from main-prompt.md Part VI) must be resolved before release.

**RELEASE BLOCKER** — any failing Critical control (F-01/02/03/04) or missing audit reconstruction capability. **Do not go live.**

---

## Canary Cutover Plan

Document in `docs/cutover-plan.md`:

### Step 1 — Deploy Prod (Kill-Switch On)
- Deploy all infra + code with `AGENT_ENABLED=false`
- Verify deployment health (dashboards, Function app running, Cosmos connected)

### Step 2 — Canary (1 certificate)
- Enable for one **low-risk hostname** (`api.dev.example.com`)
- Set `AGENT_ENABLED=true` in prod
- Monitor one complete renewal end-to-end (observe all 6 steps)
- Check audit reconstruction for the canary `workflow_id`
- If anything fails: kill-switch to `AGENT_ENABLED=false`; investigate; fix; restart

### Step 3 — Limited Cohort (5–10 certificates)
- Expand to a small cohort of low-risk hostnames
- Run for 1–2 business days
- Monitor: dashboards, alerts, audit completeness, no duplicates

### Step 4 — Full Fleet
- Enable for all SSL renewals in scope
- Daily audit review for 30 days post-cutover
- Manual runbook stays authoritative alongside the agent for 30 days

### Rollback Procedure
1. `az functionapp config appsettings set ... --settings AGENT_ENABLED=false`
2. Notify SSL team: manual runbook is active
3. Investigate and fix before re-enabling

---

## Sign-Off Record (fill before going live)

| Role | Name | Date | Signature |
|------|------|------|-----------|
| SSL / Platform team | | | |
| Product Director | | | |
| PKI team (Client) | | | |
| CAB / Change management | | | |
| Security & Compliance | | | |
| SRE / On-call lead | | | |

---

## Acceptance Criteria

- All checklists above are 100% complete with evidence
- Go/No-Go decision recorded and signed off
- Canary renewal completes with full audit chain reconstructable
- 30-day manual fallback documented and operational

---

## Verification

- Evidence pack reviewed by at least two engineers not on the implementation team
- Canary `workflow_id` is reconstructable end-to-end via the audit reconstruction query
- Go decision + sign-off document archived in `docs/go-live-evidence-pack.md`
