# RUNBOOK — Autonomous SSL Certificate Renewal Agent

**Audience:** SRE team, on-call engineers  
**Classification:** Internal — Operations  
**Version:** 1.0  

---

## Quick Reference

| System | URL/Resource |
|--------|-------------|
| Azure Portal | [portal.azure.com](https://portal.azure.com) → `ssl-renewal-rg-prod` |
| App Insights | Monitor → `ssl-prod-ai` |
| Cosmos DB | `ssl-prod-cosmos` → Database: `ssl_renewal` |
| Key Vault | `ssl-prod-hsm` (Managed HSM) |
| Function App | `ssl-prod-func` |
| Service Bus | `ssl-prod-sb` → Queue: `ssl-renewals` |
| Logs | Log Analytics workspace `ssl-prod-law` |

---

## Kill-Switch — Emergency Halt

Use this when you need to stop all new renewal processing **immediately**.

```bash
# Step 1 — Set kill-switch in Key Vault (takes effect on next Function invocation)
az keyvault secret set \
  --vault-name ssl-prod-hsm \
  --name orchestrator-enabled \
  --value false

# Step 2 — Verify: new HTTP triggers should return 503
curl -X POST https://ssl-prod-func.azurewebsites.net/api/orchestrate \
  -H "x-functions-key: $FUNC_KEY" \
  -d '{"test": true}'
# Expected: 503 Service Unavailable

# Step 3 — Notify the Product Director and SRE channel
# Step 4 — Re-enable when issue is resolved:
az keyvault secret set \
  --vault-name ssl-prod-hsm \
  --name orchestrator-enabled \
  --value true
```

**In-flight workflows are NOT affected** — kill-switch only blocks new triggers.  
Active workflows continue to their current state (may need manual review).

---

## Alert Response Playbooks

### Alert: Renewal Failure Rate > 5%

1. Open App Insights → Failures → filter by `cloud_RoleName = "ssl-renewal-agent"`
2. Check `workflow_id` of failed workflows → query Cosmos: `SELECT * FROM c WHERE c.workflow_id = @wf AND c.state = "FAILED"`
3. Look at the `retry.escalations` field — if > 1, the agent already attempted recovery
4. Common causes:
   - PKI mailbox down → check `mcp_graph_mail` error logs
   - Jira rate limit → check `JIRA_LIMITER` logs for `throttled=true`
   - Cosmos write failure → check `cosmos_throttled_requests` metric
5. If systemic: activate kill-switch, page Product Director

---

### Alert: Consecutive Tool Error Halt (G3)

This fires when `PolicyMiddleware` has halted a workflow after 2 consecutive tool errors.

1. Find the affected workflow:
   ```kql
   traces
   | where message has "Halting" and message has "consecutive_errors"
   | extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
   | order by timestamp desc
   | take 10
   ```
2. Check the 2 failed tool calls in the audit log:
   ```bash
   az cosmosdb sql query \
     --account-name ssl-prod-cosmos \
     --database-name ssl_renewal \
     --container-name audit_log \
     --query "SELECT * FROM c WHERE c.workflow_id = 'wf_XXXX' ORDER BY c.seq DESC" \
     --output table
   ```
3. If the tool errors are transient (network blip): manually reset `consecutive_errors` by updating the workflow state and re-triggering via Service Bus.
4. If the tool errors are persistent: engage the relevant system team (PKI, Jira, ServiceNow).

---

### Alert: Approval SLA Breach (> 48 hours)

1. Find pending approvals:
   ```kql
   traces
   | where message has "approval_timeout"
   | extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
   | order by timestamp desc
   ```
2. The agent auto-escalates to `pd_delegate_email` after 48 hours (see `approval_tool.py`).
3. If the delegate also hasn't responded: email both PD and delegate directly, attach the Jira ticket and Teams card link from the workflow state in Cosmos.
4. If the certificate is < 7 days from expiry: escalate to emergency CAB process.

---

### Alert: Schema Drift Detected (MCP)

This fires when a Jira or Dynatrace MCP tool changes its schema unexpectedly.

1. `drift_check.py` will have logged: `mcp_schema_drift tool=<name> expected=<hash> actual=<hash>`
2. All workflows that use the affected tool are halted (fail-closed).
3. Review the new schema: check if the change is safe (e.g. new optional field).
4. If safe: update the pinned hash in `drift_check.py`, open PR, get Bob + human review, deploy.
5. If not safe: investigate if the MCP server was compromised (G5 concern). Escalate to Security.

---

### Alert: Certificate Expiring in < 14 Days, No Active Workflow

1. Check if the cert alert was missed by Dynatrace (dead-letter queue):
   ```bash
   az servicebus queue show \
     --name ssl-renewals \
     --namespace-name ssl-prod-sb \
     --query deadLetterMessageCount
   ```
2. If dead-lettered: retrieve and replay the message:
   ```bash
   # Use the dead-letter review queue 'ssl-renewals-dlq-review'
   # Replay via the /api/orchestrate endpoint with the alert payload
   ```
3. If Dynatrace never sent the alert: manually trigger via the web console or API.
4. Escalate to the Dynatrace team if alert pipeline is broken.

---

## Manual Workflow Trigger

If Dynatrace alert is not received, trigger manually:

```bash
curl -X POST https://ssl-prod-func.azurewebsites.net/api/orchestrate \
  -H "x-functions-key: $FUNC_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "workflow_id": "wf_manual_001",
    "cn": "api.prod.example.com",
    "san": ["api.prod.example.com", "api-int.prod.example.com"],
    "owning_application": "Orders-API",
    "alert": {
      "source": "manual",
      "problem_id": "MANUAL-001",
      "severity": "HIGH"
    }
  }'
```

---

## Query Workflow State

```bash
# Via Function App status endpoint
curl "https://ssl-prod-func.azurewebsites.net/api/status?workflow_id=wf_001" \
  -H "x-functions-key: $FUNC_KEY"

# Direct Cosmos query (SRE access required)
az cosmosdb sql query \
  --account-name ssl-prod-cosmos \
  --database-name ssl_renewal \
  --container-name workflow_state \
  --query "SELECT * FROM c WHERE c.workflow_id = 'wf_001'"
```

---

## Verify Audit Hash Chain

```python
# Run locally with SRE credentials
import asyncio
from src.persistence.cosmos_repo import CosmosRepo, verify_hash_chain

async def check():
    repo = CosmosRepo()
    chain = await repo.get_audit_chain("wf_001")
    print(f"Chain length: {len(chain)}")
    print(f"Chain valid: {verify_hash_chain(chain)}")

asyncio.run(check())
```

---

## Escalation Contacts

| Role | Contact | When to escalate |
|------|---------|-----------------|
| On-call SRE | PagerDuty `ssl-renewal-sre` | Any Sev1/Sev2 alert |
| Product Director | `pd@example.com` | Approval SLA breach, kill-switch activation |
| Security team | `security@example.com` | G5 schema drift, G7 key export attempt, G8 secret leak |
| PKI team | `pki@example.com` | PKI mailbox down, cert mismatch (G2 failure) |
| Azure Support | Premium support ticket | Azure service outages (Key Vault, Cosmos, Foundry) |
