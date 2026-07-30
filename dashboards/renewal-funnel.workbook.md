# Renewal Funnel Dashboard — Azure Workbook

This file is a reference template for the Azure Workbook that visualises the
renewal funnel. Import it into your Azure Monitor Workbooks gallery via:

  Azure Portal → Monitor → Workbooks → New → Advanced Editor → paste JSON

---

## KQL Queries for Each Panel

### 1. Renewal Funnel (state drop-off counts)

```kql
traces
| where timestamp > ago(7d)
| where message has "workflow_state"
| extend state = extract("state=([A-Z_]+)", 1, message)
| where isnotempty(state)
| summarize count() by state
| order by count_ desc
```

### 2. Mean Cycle Time (ALERT_RECEIVED → COMPLETE, last 7 days)

```kql
let start = traces
  | where timestamp > ago(7d)
  | where message has "state=ALERT_RECEIVED"
  | extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
  | project wf_id, start_time = timestamp;
let done = traces
  | where timestamp > ago(7d)
  | where message has "state=COMPLETE"
  | extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
  | project wf_id, end_time = timestamp;
start
| join kind=inner done on wf_id
| extend cycle_hours = datetime_diff('hour', end_time, start_time)
| summarize mean_hours = avg(cycle_hours), p95_hours = percentile(cycle_hours, 95)
```

### 3. Stuck Workflows (not updated in > 24h, not terminal)

```kql
traces
| where timestamp > ago(48h)
| where message has "workflow_state"
| extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
| extend state = extract("state=([A-Z_]+)", 1, message)
| where state !in ("COMPLETE", "FAILED", "REJECTED")
| summarize last_update = max(timestamp) by wf_id, state
| where last_update < ago(24h)
| project wf_id, state, last_update, hours_since_update = datetime_diff('hour', now(), last_update)
| order by hours_since_update desc
```

### 4. Tool Error Rate per Tool (last 24h)

```kql
traces
| where timestamp > ago(24h)
| where message has "tool_call"
| extend tool = extract("tool=([^ ]+)", 1, message)
| extend status = extract("status=([^ ]+)", 1, message)
| summarize total = count(), errors = countif(status == "error") by tool
| extend error_rate_pct = round(100.0 * errors / total, 1)
| order by error_rate_pct desc
```

### 5. Approval SLA (time from CSR_REQUESTED to APPROVED, last 7d)

```kql
let req = traces
  | where timestamp > ago(7d)
  | where message has "state=CSR_REQUESTED"
  | extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
  | project wf_id, req_time = timestamp;
let approved = traces
  | where timestamp > ago(7d)
  | where message has "state=APPROVED"
  | extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
  | project wf_id, appr_time = timestamp;
req
| join kind=leftouter approved on wf_id
| extend approval_hours = iif(isnotnull(appr_time), datetime_diff('hour', appr_time, req_time), int(null))
| summarize
    mean_hours = avg(approval_hours),
    pending = countif(isempty(approval_hours)),
    total = count()
```

### 6. Consecutive Tool Error Halts (G3, last 7d)

```kql
traces
| where timestamp > ago(7d)
| where message has "Halting" and message has "consecutive_errors"
| extend wf_id = extract("workflow_id=([^ ]+)", 1, message)
| summarize halt_count = count() by bin(timestamp, 1d)
| order by timestamp asc
```

### 7. Batch Throughput (renewals/hour, last 7d)

```kql
traces
| where timestamp > ago(7d)
| where message has "batch_coordinator" and message has "complete"
| extend batch_size = toint(extract("total=([0-9]+)", 1, message))
| summarize total_renewals = sum(batch_size) by bin(timestamp, 1h)
| order by timestamp asc
```

---

## Workbook JSON Skeleton

For a full Workbook definition, use the Azure Portal to:

1. Open Monitor → Workbooks → New
2. Add each KQL query above as a separate **Query** step
3. Set visualisation: Funnel (panel 1), Line chart (panels 2, 7), Grid (panels 3, 4, 5, 6)
4. Save as "SSL Renewal — Operational Dashboard"

Export the JSON via **Advanced Editor** and save to `dashboards/renewal-funnel.workbook.json`.

---

## Power BI Report (Approvals)

Connect Power BI to the Log Analytics workspace and use the following M query:

```m
let
    Source = AzureDataExplorer.Contents(
        "<logAnalyticsWorkspaceId>",
        [Query="traces | where message has 'approval' | project timestamp, message"]
    )
in
    Source
```

Build a bar chart of approval latency by `pd_approver_email` and a trend line of
`approval_pending_count` over time. Export as `dashboards/power-bi-approvals.pbix`.
