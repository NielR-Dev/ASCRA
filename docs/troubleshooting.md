# Troubleshooting Guide — SSL Certificate Renewal Agent

---

## Stuck Workflow

**Symptom:** `workflow_state.updated_at` is > 24 hours ago and the state is not terminal.

**Diagnosis:**

```bash
# Find stuck workflows (App Insights KQL)
az monitor log-analytics query \
  --workspace ssl-prod-law \
  --analytics-query "
    traces
    | where timestamp > ago(48h)
    | where message has 'workflow_state'
    | extend wf_id = extract('workflow_id=([^ ]+)', 1, message)
    | extend state = extract('state=([A-Z_]+)', 1, message)
    | where state !in ('COMPLETE', 'FAILED', 'REJECTED')
    | summarize last_update = max(timestamp) by wf_id, state
    | where last_update < ago(24h)
  "
```

**Resolution:**

1. Check the audit log for the last recorded action.
2. If stuck at `CSR_REQUESTED` (waiting for approval): email PD + delegate directly.
3. If stuck at `APPROVED` (waiting for PKI): check `mcp_graph_mail` logs for send failure.
4. If stuck at `PKI_REPLIED` (waiting for verify): check `verify_cer` logs for parse error.
5. Manual re-trigger (if safe): POST the workflow payload to `/api/orchestrate`.

---

## Certificate Verifier Failure (G2)

**Symptom:** `verify_cer` returns `pass_=False` — workflow transitions to `FAILED`.

**Diagnosis:**

```python
# Reconstruct locally with the downloaded CER
from src.tools.verify_cer import verify_cer
import base64

with open("wf_001.cer", "rb") as f:
    cer_b64 = base64.b64encode(f.read()).decode()

result = verify_cer(cer_b64, expected_cn="api.example.com",
                    expected_san=["api.example.com"], workflow_id="wf_001")
print(result)
# VerifyResult(pass_=False, checks={'cn_match': False, 'san_match': True, ...})
```

**Common causes:**

| Check | Cause | Fix |
|-------|-------|-----|
| `cn_match=False` | PKI issued cert for wrong CN | Contact PKI team; request re-issuance |
| `san_match=False` | SAN list mismatch | Verify the CSR had correct SANs; check Jira ticket |
| `valid_days=False` | Cert validity < 365 days | PKI issued short-lived cert; request standard validity |
| `not_expired=False` | Cert is already expired | PKI error; request new cert immediately |
| `parse_error=True` | CER file corrupted | Request PKI to resend; check email attachment encoding |

---

## MCP Schema Drift

**Symptom:** `mcp_schema_drift` event in App Insights.

**Diagnosis:** `drift_check.py` has detected that the live MCP tool schema hash doesn't match the pinned hash.

```bash
# Check which tool drifted
grep "mcp_schema_drift" <(az monitor log-analytics query \
  --workspace ssl-prod-law \
  --analytics-query "traces | where message has 'mcp_schema_drift' | order by timestamp desc | take 10" \
  --output json)
```

**Resolution:**

1. Review the actual schema change at the MCP server.
2. If the change is safe (new optional field, etc.): update the hash in `drift_check.py` and deploy.
3. If the change is suspicious (removed required field, new mandatory action): escalate to Security (G5).
4. All workflows using the affected tool are halted until the hash is updated.

---

## PKI Reply Delay

**Symptom:** Workflow stuck in `APPROVED` state > 5 business days.

**Diagnosis:**

```kql
traces
| where timestamp > ago(10d)
| where message has "pki_reply_wait" and message has "reminders_sent"
| extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
| extend reminders = toint(extract("reminders_sent=([0-9]+)", 1, message))
| order by timestamp desc
```

**Resolution:**

1. Check if the PKI email was actually sent (audit log: `action=email_sent_to_pki`).
2. Check the PKI mailbox is reachable: send a test email.
3. Contact the PKI team directly with the Jira ticket number.
4. If the cert is < 7 days from expiry: escalate to emergency CAB process.

---

## Service Bus Queue Backlog

**Symptom:** `ssl-renewals` queue depth growing; Function App not processing.

**Diagnosis:**

```bash
az servicebus queue show \
  --name ssl-renewals \
  --namespace-name ssl-prod-sb \
  --resource-group ssl-renewal-rg-prod \
  --query "{depth: messageCount, dlq: deadLetterMessageCount, size: sizeInBytes}"
```

**Resolution:**

1. Check Function App scaling — Elastic Premium auto-scales; verify `maxElasticWorkerCount`.
2. Check for Function App health failures: `az functionapp show --name ssl-prod-func --resource-group ssl-renewal-rg-prod --query state`
3. Check `max_concurrent_renewals` setting — default 20; increase if queue is healthy.
4. If queue contains invalid messages: purge using Azure Service Bus Explorer.

---

## Cosmos DB Throttling

**Symptom:** `429 TooManyRequests` errors in Cosmos operations.

**Resolution:**

```bash
# Check current RU consumption
az monitor metrics list \
  --resource /subscriptions/$SUB/resourceGroups/ssl-renewal-rg-prod/providers/Microsoft.DocumentDB/databaseAccounts/ssl-prod-cosmos \
  --metric TotalRequestUnits \
  --interval PT1M

# If consistently hitting limit, increase throughput
az cosmosdb sql container throughput update \
  --account-name ssl-prod-cosmos \
  --database-name ssl_renewal \
  --name workflow_state \
  --throughput 1000
```

---

## Function App Cold Start / Timeout

**Symptom:** Long latency on first request; `Function timeout reached`.

**Resolution:**

1. Check plan: Elastic Premium EP1/EP2 should have pre-warmed instances.
2. Verify `WEBSITE_RUN_FROM_PACKAGE=1` is set (reduces cold start).
3. Increase `functionTimeout` in `host.json` if orchestration is legitimately long.
4. Add an Application Insights availability test to keep the Function warm.
