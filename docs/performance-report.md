# Performance Report — SSL Certificate Renewal Agent

**Version:** 1.0 (template — fill in actual numbers after load test run)  
**Test environment:** UAT (ssl-renewal-rg-uat)  

---

## SLO Summary

| Metric | SLO Target | Measured (fill in) | Status |
|--------|-----------|-------------------|--------|
| Autonomous step latency (p95) | < 60s | — | ☐ |
| Alert → CSR_REQUESTED (p95) | < 5 min | — | ☐ |
| Approval → PKI email (p95) | < 2 min | — | ☐ |
| CER received → verified verdict | < 60s | — | ☐ |
| Duplicate external side effects | 0 | 0 (unit tested) | ✅ |
| Batch throughput (sustained) | ≥ 100 renewals/hour | — | ☐ |
| Concurrent children cap | ≤ max_concurrent_renewals | Verified in unit tests | ✅ |
| Expiry-wave (100 certs) time-to-submitted | < 1 business day | — | ☐ |
| Per-lane quota breaches (PKI/Jira/SNOW) | 0 | 0 (unit tested) | ✅ |
| Sibling isolation (one failure → others continue) | Always | Verified in unit tests | ✅ |

---

## Unit-Level Performance Test Results

Run: `pytest tests/test_performance.py -v`

| Test | Result | Notes |
|------|--------|-------|
| `test_concurrent_children_bounded_by_semaphore` | ✅ PASS | Cap of 5; peak measured ≤ 5 |
| `test_sibling_isolation_one_failure_does_not_abort_others` | ✅ PASS | 9/10 succeed when 1 fails |
| `test_no_duplicate_side_effects_on_retry` | ✅ PASS | 10 retries → 1 side effect call |
| `test_rate_limiter_throttles_to_cap` | ✅ PASS | 3 acquires within 60/min window |
| `test_batch_20_certs_completes_within_timeout` | ✅ PASS | 20 × 10ms work < 2s elapsed |

---

## Load Test Procedure (run against UAT before go-live)

### Prerequisites
- UAT environment fully deployed
- Function App warm (EP1/EP2 Elastic Premium)
- Cosmos DB provisioned throughput (1000 RU/s minimum)

### Test 1 — Expiry Wave (100 certificates)

```bash
python -m scripts.load_test.expiry_wave \
  --certs 100 \
  --concurrency 20 \
  --endpoint https://ssl-uat-func.azurewebsites.net/api/orchestrate \
  --func-key $FUNC_KEY \
  --timeout 3600   # 1 business day = 3600s in this synthetic test
```

**Assertions:**
- All 100 workflows reach a terminal state (COMPLETE, FAILED, or REJECTED)
- `duplicate_jira_tickets` = 0
- `duplicate_pki_emails` = 0
- `rate_limit_breaches` = 0
- `completion_rate` ≥ 95%

### Test 2 — Batch Throughput

```bash
python -m scripts.load_test.batch_throughput \
  --rate 20 \       # 20 certs per batch
  --interval 10 \   # new batch every 10s
  --duration 60     # run for 60s → 6 batches × 20 certs = 120 total
```

**Expected:** ≥ 100 renewals/hour = ≥ 1.67/second sustained

### Test 3 — Idempotency Under Network Partition

Simulate a transient failure after Jira ticket creation but before result storage:

```bash
python -m scripts.load_test.idempotency_chaos \
  --workflows 10 \
  --fault-at jira_create \
  --retry-count 5
```

**Expected:** Exactly 1 Jira ticket per workflow (no duplicates), all 10 workflows complete.

---

## Timer Behaviour (unit tested in test_timers.py)

| Timer | Threshold | Test | Result |
|-------|-----------|------|--------|
| Approval escalation | 48h | `test_escalated_at_exact_timeout` | ✅ PASS |
| Approval no-escalate | < 48h | `test_not_escalated_before_timeout` | ✅ PASS |
| PKI reminder 1 | 24h | `test_first_reminder_at_24h` | ✅ PASS |
| PKI reminder 2 | 72h | `test_second_reminder_at_72h` | ✅ PASS |
| No reminder after both sent | > 72h | `test_no_reminder_after_both_sent` | ✅ PASS |
| PKI overdue | > 5 days | `test_pki_overdue_after_5_days` | ✅ PASS |

---

## Idempotency Design

Every external side effect is guarded by an idempotency key in Cosmos DB:

| Side effect | Idempotency key | TTL |
|-------------|----------------|-----|
| Jira ticket creation | `jira_create_{workflow_id}` | 30 days |
| PKI email send | `email_send_{workflow_id}` | 30 days |
| ServiceNow CHG creation | `chg_create_{workflow_id}` | 30 days |

On retry, `CosmosRepo.check_idempotency(key)` returns the stored result without
re-executing the side effect. This is verified in `tests/test_persistence.py::TestIdempotency`.

---

## Rate Limiter Configuration

| Lane | Default limit | Config var | Purpose |
|------|--------------|-----------|---------|
| PKI email | 10/min | `PKI_RATE_PER_MIN` | Prevent flooding PKI mailbox |
| Jira API | 60/min | `JIRA_RATE_PER_MIN` | Stay within Jira Cloud rate limit |
| ServiceNow | 30/min | `SNOW_RATE_PER_MIN` | Stay within ServiceNow quota |

Rate limits are per-Function-instance. With 20 concurrent workers × 10/min PKI limit = 200/min
burst capacity across the fleet (tune if PKI quota is lower).
