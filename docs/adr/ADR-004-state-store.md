# ADR-004 — State Store: Azure Cosmos DB (NoSQL)

> **Status:** Accepted  
> **Date:** 2026-07-28  
> **Decision makers:** Architecture team, IBM Bob

---

## Context

The agent requires a persistent state store to support:

1. **Workflow state** (`workflow_state` container): one document per renewal; low-latency read/write on `workflow_id`; must survive orchestrator restarts without losing in-progress work.
2. **Audit log** (`audit_log` container): append-only; ordered by `seq`; hash-chained for tamper evidence; query by `workflow_id` for full reconstruction.
3. **Idempotency keys** (`idempotency` container): short-TTL (30 days); prevents duplicate Jira/SNOW tickets and emails on retry.
4. **Batch record** (`batch` container): aggregates child renewal outcomes for a wave; one document per batch; fan-in updates are idempotent.

Additional requirements:
- **Bursty access pattern:** expiry waves cause sudden spikes (100+ concurrent reads/writes vs. near-zero baseline). The store must autoscale without manual intervention.
- **7-year retention for CER artifacts:** Blob Storage handles the actual files (WORM); Cosmos stores metadata and audit records (no TTL on audit/state).
- **Continuous backup (PITR):** required for DR — can restore to any second within a 7-day window.
- **Multi-partition append:** the hash chain per `workflow_id` is within a single logical partition, enabling Cosmos transactional batch for co-located audit writes.
- **Schema evolution:** the audit payload will grow over time (new fields per phase); the store must accommodate additive changes without migration downtime.

---

## Decision

**Chosen: Azure Cosmos DB for NoSQL (multi-container, single database `ssl_renewal`).**

| Container | Partition key | TTL | Notes |
|-----------|--------------|-----|-------|
| `workflow_state` | `/workflow_id` | Off (retain) | Point reads/writes; autoscale RU |
| `audit_log` | `/workflow_id` | Off (retain) | Append-only; ordered by `seq`; hash chain per partition |
| `idempotency` | `/idempotency_key` | 30 days | Unique constraint prevents duplicate side effects |
| `batch` | `/batch_id` | Off (retain) | Fan-in aggregate; children reference via `batch_id` on `workflow_state` |

**Key reasons:**
- **Low-latency point reads** on `workflow_id` (< 10ms p99) — critical for the orchestrator's state check before each tool call.
- **Autoscale RU** — absorbs bursty expiry waves without manual capacity management.
- **TTL on idempotency container** — built-in 30-day expiry without a separate cleanup job.
- **PITR (continuous backup)** — 7-day point-in-time restore window satisfies DR requirements.
- **Schema flexibility (JSON/NoSQL)** — audit payloads grow with new fields via `schema_version`; no ALTER TABLE statements; forward-only migrations via a versioned upgrader.
- **Cosmos transactional batch** — state + audit writes within one `workflow_id` partition are co-located; transactional batch eliminates partial-write risk.
- **No cross-partition transactions needed** — each renewal is a single partition; batch aggregation is idempotent fan-in (no ACID required across children).

---

## Alternatives Considered

### Alternative 1: Azure SQL Database

**Rejected.** SQL offers strong consistency and joins, but:
- **Schema rigidity:** the evolving audit payload (new fields per phase, per version) requires ALTER TABLE or EAV anti-pattern workarounds. This friction slows velocity and risks data loss if a migration is incomplete.
- **Scaling model:** SQL scales vertically (DTU/vCore tiers) with predictable latency under load — not ideal for bursty expiry waves without over-provisioning.
- **No native TTL:** idempotency key expiry requires a scheduled cleanup job.
- **No PITR included by default** for lower tiers; requires Business Critical tier for the SLA we need.
- **No built-in legal hold / immutability** — CER retention would need separate Blob Storage anyway.

SQL would be the right choice for purely relational, stable-schema data. The audit payload is neither.

### Alternative 2: Azure Table Storage

**Rejected.** Table Storage is cost-optimised for simple key-value lookups but:
- No ordered-range queries (needed for `audit_log` ordered by `seq` within `workflow_id`).
- No TTL natively.
- No PITR.
- No transactional batch across tables.
- Poor developer experience for the complex query patterns needed for dashboards.

### Alternative 3: Redis Cache (Azure Cache for Redis)

**Rejected for primary state store.** Redis is in-memory and not durable by default. While it could serve as a caching layer in front of Cosmos, it cannot be the authoritative state store for a healthcare-grade system with 7-year audit requirements. Redis persistence options (RDB / AOF) add operational complexity without the compliance features (PITR, immutability, Purview lineage) that Cosmos provides.

### Alternative 4: Azure Durable Functions / Storage Table (built-in persistence)

**Partially adopted.** The Batch Coordinator is implemented as a Durable Function, which uses Azure Storage Tables internally for orchestration state. However, this is the *coordinator's* durable execution state (which step it is on), not the business audit trail. The authoritative `workflow_state` and `audit_log` are in Cosmos so they are:
- Queryable by `cn`, `owning_application`, `state` for dashboards.
- Hash-chained and tamper-evident.
- Accessible to the audit reconstruction procedure (P14 compliance.md).
- Not tied to the Durable Functions runtime format.

---

## Data Model Summary

```
ssl_renewal (Cosmos DB)
├── workflow_state  (one doc per renewal; PK = workflow_id)
├── audit_log       (N docs per renewal; PK = workflow_id; ordered by seq; hash-chained)
├── idempotency     (one doc per external side-effect key; PK = idempotency_key; TTL 30d)
└── batch           (one doc per expiry wave; PK = batch_id; children link via batch_id)

Blob Storage (cer-artifacts)  ← CER files, WORM / legal hold / 7-year retention
Key Vault (HSM)               ← Private keys (non-exportable); key ID stored in workflow_state
```

No private key material, full CSR bytes, or full CER bytes are stored in Cosmos. Only the **Key Vault key ID**, **SHA-256 of the CSR**, and the **Blob URL of the CER** are recorded — data minimization enforces G7 and G8.

---

## Consequences

| Consequence | Mitigation |
|------------|-----------|
| Cosmos is more expensive than SQL or Table Storage for small workloads | Autoscale RU (minimum 100 RU/s) keeps cost proportional to actual load; separate containers with independent RU settings |
| Hot-partition risk if `workflow_id` is non-random | IDs embed date + hostname + random 4-char suffix → good spread across partition key hash space |
| NoSQL schema flexibility can lead to inconsistent documents | `schema_version` on every document; forward-only migrations via a versioned upgrader; no mutation of historical audit records (append-only) |
| Cosmos PITR restore requires a specific recovery procedure | Documented in P14 dr-guide.md; rehearsed in go-live readiness (P15) |

**Confidence:** Medium-High. Cosmos is well-suited for this bursty, schema-evolving, low-latency workload. The primary uncertainty is RU sizing under a large expiry wave — mitigated by autoscale + load testing (P13).
