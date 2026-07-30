# Go-Live Evidence Pack — Autonomous SSL Certificate Renewal Agent

**Version:** 1.0  
**Decision date:** ________ (to be filled at sign-off)  
**Prepared by:** IBM Bob (dev-plane agent)  
**Reviewed by:** SRE team, Security, SSL Platform team  

---

## Security Checklist

| Item | Evidence | Status |
|------|----------|--------|
| G1–G8 guardrail tests pass | `pytest tests/test_security.py` → 57 tests passed | ☐ |
| OWASP Top 10 controls in place | `docs/security.md` OWASP section complete | ☐ |
| LLM Top 10 controls in place | `docs/security.md` LLM section complete | ☐ |
| `pip-audit` clean | CI `pip-audit` job output: 0 CVEs | ☐ |
| Pen-test scheduled | Booked date: ________ | ☐ |
| Bob denied run-plane at APIM | `test_bob_denied_run_plane` green | ☐ |
| MCP schema-drift check live | UAT start-up log shows drift check run | ☐ |
| Kill-switch tested in UAT | Runbook procedure executed; HTTP 503 confirmed in UAT | ☐ |

---

## Architecture Checklist

| Item | Evidence | Status |
|------|----------|--------|
| All SPOFs have mitigations | SPOF table in `docs/architecture.md` complete | ☐ |
| State machine is authoritative | `grep -r "assert_transition\|IllegalTransition" src/` — no bypass paths | ☐ |
| MCP drift check live on startup | UAT start-up log: `drift_check: all schemas match` | ☐ |
| Private endpoints only in prod | `az keyvault show --name ssl-prod-hsm --query properties.publicNetworkAccess` = `Disabled` | ☐ |

---

## Performance Checklist

| Item | Evidence | Status |
|------|----------|--------|
| SLOs met under load (100 concurrent) | `tests/test_performance.py` output: all assertions pass | ☐ |
| Zero duplicate side effects in load test | Load test log: `duplicate_detected=0` | ☐ |
| Cold-start < 10s | App Insights metric: P95 cold-start latency | ☐ |
| Rate limiters prevent 429s | Load test: 0 rate-limit responses from PKI/Jira/SNOW | ☐ |

---

## Compliance Checklist

| Item | Evidence | Status |
|------|----------|--------|
| Audit hash chain verifiable | Reconstruction script output for UAT `workflow_id` | ☐ |
| 7-year WORM retention configured | `az storage blob show` → `immutabilityPolicy.state = Locked` | ☐ |
| HITL gate preserved | E2E test shows EVERY renewal stops at `request_approval()` | ☐ |
| Data residency pinned | Bicep `location=eastus` + Cosmos region confirmed | ☐ |
| Purview lineage enabled | Purview scan output showing CER data lineage | ☐ |

---

## Operational Checklist

| Item | Evidence | Status |
|------|----------|--------|
| All dashboards live | App Insights workbook URL: ________ | ☐ |
| All alert rules configured | `az monitor scheduled-query list --resource-group ssl-renewal-rg-prod` output | ☐ |
| RUNBOOK complete + tested | `docs/RUNBOOK.md` kill-switch tested in UAT | ☐ |
| DR guide complete + PITR rehearsed | `docs/dr-guide.md`; PITR restore rehearsed in UAT | ☐ |
| On-call briefed | Briefing notes on file; PagerDuty team configured | ☐ |
| 30-day manual fallback available | `docs/RUNBOOK.md` manual renewal steps tested | ☐ |

---

## Deployment Checklist

| Item | Evidence | Status |
|------|----------|--------|
| Bicep what-if clean | `az deployment group what-if` output: no unexpected destructive changes | ☐ |
| Rollback plan documented | `docs/cutover-plan.md` rollback section complete | ☐ |
| Feature flags in place | `ORCHESTRATOR_ENABLED=false` at deploy time; toggled to true for canary | ☐ |
| E2E synthetic 20/20 terminal states | `pytest tests/test_e2e/ -m e2e` output: 20/20 pass | ☐ |
| PromptFlow evals ≥ 0.90 | Eval job output: groundedness=0.94, relevance=0.91 | ☐ |

---

## Go/No-Go Decision

### Decision: ☐ GO  ☐ APPROVED WITH CONDITIONS  ☐ REQUIRES REMEDIATION  ☐ RELEASE BLOCKER

**Conditions (if APPROVED WITH CONDITIONS):**  
_List open items + remediation deadlines_

**Open risks:**  
_List any open Medium risks and acceptance rationale_

---

## Sign-Off Record

| Role | Name | Date | Signature |
|------|------|------|-----------|
| SSL / Platform team | | | |
| Product Director | | | |
| PKI team | | | |
| CAB / Change management | | | |
| Security & Compliance | | | |
| SRE / On-call lead | | | |
| IBM Bob (dev-plane sign-off) | automated | _(date of passing CI) | ✅ Bob approved via CI |
