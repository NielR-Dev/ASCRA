# Phase 5 — Data Design

> **Pre-read:** [00-context.md](00-context.md) · depends on P3 output
> **Deliverable:** Cosmos schemas, hash-chain utility, Bicep for data stores
> **Task IDs:** T11
> **Effort estimate:** ~4 person-days

---

## Your Task

Design and implement the data layer: Cosmos containers + document schemas, the audit hash chain, the idempotency store, the batch record, and the Blob WORM config. Write `cosmos_repo.py` and `blob_repo.py`.

---

## What to Produce

1. **`src/persistence/cosmos_repo.py`** — workflow state, audit log, idempotency, batch containers
2. **`src/persistence/blob_repo.py`** — CER WORM upload/read
3. **`infra/cosmos.bicep`** — Cosmos DB + 4 containers with correct settings
4. **`infra/storage.bicep`** — Blob account with WORM/legal hold
5. **`tests/test_persistence.py`** — unit tests

---

## Data Stores

| Store | Container | Purpose |
|-------|-----------|---------|
| Cosmos `ssl_renewal` | `workflow_state` | One document per renewal; current state + context |
| Cosmos `ssl_renewal` | `audit_log` | Append-only audit events with hash chain |
| Cosmos `ssl_renewal` | `idempotency` | Idempotency keys for external side-effects |
| Cosmos `ssl_renewal` | `batch` | One document per expiry wave |
| Blob `cer-artifacts` | — | Received CER files; WORM/legal hold; 7-yr retention |
| Key Vault (HSM) | — | Private keys + CSR signing (not your responsibility here — see P7) |

---

## `workflow_state` Document Schema

```json
{
  "id": "wf_2026-07-28_api.prod.example.com_7f3a",
  "workflow_id": "wf_2026-07-28_api.prod.example.com_7f3a",
  "batch_id": "batch_2026-07-28_wave_ca-rotation_4a1c",
  "state": "APPROVED",
  "cn": "api.prod.example.com",
  "san": ["api.prod.example.com", "api-internal.prod.example.com"],
  "owning_application": "Orders-API",
  "alert": { "source": "dynatrace", "problem_id": "P-12345", "received_at": "2026-07-28T13:02:11Z" },
  "csr": {
    "key_vault_key_id": "https://kv-ssl-hsm.vault.azure.net/certificates/wf-…/ab12",
    "csr_pem_sha256": "abc123…",
    "jira_ticket": "SSL-4821",
    "requested_at": "2026-07-28T13:05:40Z"
  },
  "approval": {
    "approver": "pd@test-domain.com",
    "decision": "APPROVED",
    "reasoning": "Matches CMDB owner + SANs",
    "decided_at": "2026-07-28T13:20:03Z",
    "card_correlation_id": "appr_9c2e"
  },
  "pki": { "email_thread_id": "AAMk…", "sent_at": "…", "reply_at": null, "reminders_sent": 0 },
  "verification": { "pass": null, "checks": {}, "cer_blob_url": null, "verified_at": null },
  "change": { "chg_number": null, "created_at": null },
  "retry": { "rounds": 0, "escalations": 0 },
  "idempotency_keys": { "jira_create": "…", "email_send": "…", "chg_create": "…" },
  "thread_id": "thread_abc123",
  "created_at": "2026-07-28T13:02:12Z",
  "updated_at": "2026-07-28T13:20:03Z",
  "schema_version": 1
}
```

**Critical — what you must NOT store in Cosmos:**
- Private key material (store only `key_vault_key_id`)
- Full CSR PEM body (store only `csr_pem_sha256`)
- Full CER bytes (store only `cer_blob_url`)

This enforces data minimization (G7/G8).

---

## `audit_log` Document Schema

```json
{
  "id": "audit_wf_…_0007",
  "workflow_id": "wf_2026-07-28_api.prod.example.com_7f3a",
  "seq": 7,
  "timestamp": "2026-07-28T13:20:03Z",
  "actor": "pd@test-domain.com",
  "action": "approval_decision",
  "tool": "record_approval_decision",
  "input_summary": { "cn": "api.prod.example.com" },
  "output_summary": { "decision": "APPROVED" },
  "state_before": "CSR_REQUESTED",
  "state_after": "APPROVED",
  "correlation_id": "thread_abc123",
  "hash_prev": "sha256_of_previous_record",
  "hash_self": "sha256(canonical(this_record) + hash_prev)",
  "schema_version": 1
}
```

**Tamper resistance:** `hash_self = SHA256(canonical_json(record_without_hash_self) + hash_prev)` — a hash chain per `workflow_id`. First record uses `hash_prev = "genesis"`.

**Append-only:** never mutate historical audit records — only add new ones.

---

## `batch` Document Schema

```json
{
  "id": "batch_2026-07-28_wave_ca-rotation_4a1c",
  "batch_id": "batch_2026-07-28_wave_ca-rotation_4a1c",
  "source": "expiry-wave",
  "created_at": "2026-07-28T13:00:00Z",
  "concurrency_limit": 20,
  "children": [
    { "workflow_id": "wf_…_api.prod.example.com_7f3a", "cn": "api.prod.example.com", "state": "APPROVED" }
  ],
  "aggregate": {
    "total": 100,
    "by_state": { "COMPLETE": 92, "FAILED": 3, "REJECTED": 2, "in_flight": 3 },
    "retries": 7, "escalations": 1
  },
  "approval": { "mode": "batch", "approver": "pd@test-domain.com", "decided_at": "2026-07-28T13:40:00Z" },
  "schema_version": 1
}
```

The batch document is an **aggregate/index** only. Each child's authoritative state + audit live in its own `workflow_state`/`audit_log` docs. Fan-in updates `aggregate` idempotently.

---

## Idempotency Container

- PK = `/idempotency_key`
- TTL = 30 days
- On write: `upsert` with the idempotency key as the document id
- On replay: return the stored result without re-executing the side effect

Used for: `jira_create`, `email_send`, `chg_create` — every external write operation.

---

## Cosmos Bicep Settings

```bicep
// infra/cosmos.bicep (key settings — implement the full module)
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-02-15-preview' = {
  properties: {
    enableAnalyticalStorage: false
    backupPolicy: { type: 'Continuous', continuousModeProperties: { tier: 'Continuous30Days' } }
    // PITR: 7-day restore window
    consistencyPolicy: { defaultConsistencyLevel: 'Session' }
    locations: [{ locationName: location, isZoneRedundant: true }]
  }
}
// Containers: workflow_state, audit_log (no TTL), idempotency (TTL 30d), batch (no TTL)
// PK for all: /workflow_id or /batch_id as appropriate
// Indexes: state, cn, owning_application, updated_at (exclude large string paths)
```

---

## Blob WORM Settings

```bicep
// infra/storage.bicep (key settings)
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  properties: {
    isVersioningEnabled: true
    // Enable soft delete + legal hold in the cer-artifacts container
  }
}
// cer-artifacts container: immutabilityPolicy { allowProtectedAppendWrites: false, immutabilityPeriodSinceCreationInDays: 2555 } // 7 years
```

---

## Hash Chain Utility (implement in `cosmos_repo.py`)

```python
import hashlib
import json

def compute_hash(record_without_hash: dict, hash_prev: str) -> str:
    """SHA-256(canonical_json(record) + hash_prev). Deterministic and reproducible."""
    canonical = json.dumps(record_without_hash, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256((canonical + hash_prev).encode()).hexdigest()
```

---

## Acceptance Criteria

- Cosmos containers created with correct PK, TTL, indexing, and PITR settings
- `workflow_state` documents pass schema validation; no private key / CSR body / CER bytes stored
- Audit records pass hash-chain re-computation (verify `hash_self` matches recomputed value)
- `idempotency` container prevents duplicate side effects on replay
- Blob `cer-artifacts` has WORM/legal-hold enabled; 7-year retention policy
- `cosmos_repo.py` exposes typed read/write/upsert operations, not raw dict blobs

---

## Verification

```bash
pytest tests/test_persistence.py -v
```

Tests must cover:
- Write a `workflow_state` document; read it back; verify schema fields present
- Write 8 audit events; re-compute hash chain from scratch; assert all `hash_self` values match
- Write with idempotency key; write again with same key; assert the second call returns prior result
- Scan test: no field in any test document matches a private-key / CSR-body regex pattern
