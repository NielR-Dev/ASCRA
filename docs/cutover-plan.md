# Cutover Plan — Autonomous SSL Certificate Renewal Agent

**Classification:** Internal — Change Management  
**Change type:** Major — New production capability  
**CAB approval required:** Yes  

---

## Overview

Phased cutover using a feature flag (`ORCHESTRATOR_ENABLED`):

```
Deploy with kill-switch ON → Canary (1 cert) → Limited cohort (5-10) → Full fleet
                                                                         ↑
                                                              30-day parallel run (manual fallback active)
```

At any stage: if anything fails, flip the kill-switch to `false` and fall back to the manual runbook.

---

## Step 1 — Deploy Prod (Kill-Switch ON)

**Duration:** ~2 hours  
**Risk:** Low — no traffic to agent

```bash
# Deploy infra with orchestrator disabled
az deployment group create \
  --resource-group ssl-renewal-rg-prod \
  --template-file infra/main.bicep \
  --parameters @infra/prod.bicepparam orchestratorEnabled=false

# Deploy Function App code
func azure functionapp publish ssl-prod-func --python --build remote

# Verify deployment
curl https://ssl-prod-func.azurewebsites.net/api/status \
  -H "x-functions-key: $FUNC_KEY"
# Expected: {"status":"healthy","orchestrator_enabled":false}
```

**Go / No-Go checkpoint:** All resources running, dashboards showing data, no errors in App Insights.

---

## Step 2 — Canary (1 Certificate)

**Duration:** 1 full renewal cycle (typically 1–3 business days)  
**Risk:** Low — single cert; manual fallback active

### Pre-conditions
- [ ] RUNBOOK available and tested
- [ ] PKI team notified: one test renewal incoming
- [ ] Product Director available for Teams approval card

### Enable for canary

```bash
# Enable orchestrator
az keyvault secret set \
  --vault-name ssl-prod-hsm \
  --name orchestrator-enabled \
  --value true

# Trigger a renewal for the designated canary hostname
curl -X POST https://ssl-prod-func.azurewebsites.net/api/orchestrate \
  -H "x-functions-key: $FUNC_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "cn": "api-canary.dev.example.com",
    "san": ["api-canary.dev.example.com"],
    "owning_application": "Canary-Test",
    "alert": {"source": "manual", "problem_id": "CANARY-001", "severity": "LOW"}
  }'
```

### Monitor canary

Watch the App Insights dashboard for the canary `workflow_id`:

```bash
# Query workflow state
curl "https://ssl-prod-func.azurewebsites.net/api/status?workflow_id=wf_CANARY-001" \
  -H "x-functions-key: $FUNC_KEY"
```

Expected progression: `ALERT_RECEIVED → PARSED → CSR_READY → CSR_REQUESTED → [PD approves] → APPROVED → PKI_REPLIED → VERIFIED → COMPLETE`

### Canary success criteria
- [ ] All 8 states reached in sequence
- [ ] Audit hash chain verifiable (run `verify_hash_chain` on the canary workflow)
- [ ] CER stored in WORM Blob
- [ ] ServiceNow CHG ticket created
- [ ] Teams completion card received
- [ ] No errors in App Insights

### If canary fails
```bash
az keyvault secret set --vault-name ssl-prod-hsm --name orchestrator-enabled --value false
# Follow RUNBOOK escalation procedure
```

---

## Step 3 — Limited Cohort (5–10 Certificates)

**Duration:** 1–2 business days  
**Risk:** Low-Medium

Enable for a cohort of low-risk, non-critical hostnames:
- Internal APIs (not customer-facing)
- Non-prod-fronting services
- Services with long remaining validity (> 30 days)

Monitor continuously:
- Zero failures
- No duplicate Jira tickets
- PKI team confirms all CSRs received correctly
- Approval SLA within 24 hours for cohort

---

## Step 4 — Full Fleet

**Duration:** Rolling over 1 week  
**Risk:** Medium (first full-scale run)

Enable for all SSL renewals in the defined scope:
- All hostnames in the Dynatrace SSL monitoring inventory
- Keep manual runbook as authoritative parallel fallback for 30 days

### 30-Day Parallel Run

For the first 30 days post-cutover:
1. Daily review of App Insights dashboard (< 5 minutes)
2. Manual spot-check: 5 random workflows/week — verify audit chain integrity
3. Weekly sync between SRE, PKI, and SSL Platform team
4. Incident threshold for reverting: > 5% failure rate OR any G1-G8 guardrail breach

---

## Rollback Procedure

If any issue arises at any step:

```bash
# Step 1 — Kill-switch (< 30 seconds)
az keyvault secret set \
  --vault-name ssl-prod-hsm \
  --name orchestrator-enabled \
  --value false

# Step 2 — Notify SSL team
# "Agent disabled. Manual renewal runbook is active. See docs/RUNBOOK.md."

# Step 3 — Investigate
# - Check App Insights for error details
# - Check Cosmos audit log for last action
# - Run verify_hash_chain for affected workflows

# Step 4 — Fix and re-validate in UAT before re-enabling
```

**Important:** In-flight renewals at the point of kill-switch will need manual completion.
Use the Cosmos workflow state to determine which step each in-flight workflow had reached.

---

## Communication Plan

| Milestone | Notification | Recipients |
|-----------|-------------|------------|
| Deploy (kill-switch ON) | Email | SSL team, SRE, PKI team |
| Canary start | Teams message | SSL team, PD (approval needed), PKI team |
| Canary success | Teams message | All stakeholders |
| Full fleet enable | Change request closed | CAB, all stakeholders |
| Any rollback | Incident created | SRE, PD, SSL team |
