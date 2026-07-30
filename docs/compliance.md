# Compliance Documentation — SSL Certificate Renewal Agent

**Classification:** Internal — Compliance  
**Frameworks:** HIPAA, ISO 27001, SOC 2 Type II  
**Retention period:** 7 years (WORM Blob + Cosmos audit log)  

---

## Guardrail → Compliance Mapping

| Guardrail | Control | HIPAA § | ISO 27001 | SOC 2 |
|-----------|---------|---------|-----------|-------|
| G1 — HITL approval | No autonomous changes without human sign-off | §164.312(a)(1) Access Control | A.9.4.2 | CC6.1 |
| G2 — Cert verification | Cryptographic integrity — wrong cert never installed | §164.312(e)(2)(ii) Encryption | A.10.1.1 | CC7.2 |
| G3 — Halt on errors | Fail-safe defaults | §164.312(b) Audit Controls | A.16.1.4 | CC7.3 |
| G4 — Audit every call | Complete audit trail | §164.312(b) Audit Controls | A.12.4.1 | CC7.1 |
| G5 — MCP output untrusted | Input validation / injection prevention | §164.312(c)(1) Integrity | A.12.2.1 | CC8.1 |
| G6 — No wildcards | Certificate policy enforcement | §164.312(e)(1) Transmission | A.10.1.1 | CC6.7 |
| G7 — Non-exportable keys | Key management — private key never leaves HSM | §164.312(e)(2)(ii) | A.10.1.2 | CC6.1 |
| G8 — No secrets in code | Credential management | §164.312(a)(2)(iv) | A.9.4.3 | CC6.3 |

---

## Audit Trail Reconstruction

Every action taken by the system is recorded in the `audit_log` Cosmos container
with a tamper-evident hash chain. To reconstruct any renewal:

### Step 1 — Retrieve audit chain for a workflow

```python
import asyncio
from src.persistence.cosmos_repo import CosmosRepo, verify_hash_chain

async def reconstruct_audit(workflow_id: str) -> None:
    repo = CosmosRepo()
    chain = await repo.get_audit_chain(workflow_id)
    valid = verify_hash_chain(chain)
    print(f"Workflow: {workflow_id}")
    print(f"Events:   {len(chain)}")
    print(f"Chain valid (no tampering): {valid}")
    for event in chain:
        print(f"  [{event['seq']:03d}] {event['timestamp']}  "
              f"{event['actor']:20s}  {event['action']:30s}  "
              f"{event['state_before']} → {event['state_after']}")

asyncio.run(reconstruct_audit("wf_001"))
```

### Step 2 — Verify the CER file is unchanged (WORM)

```bash
# Download the CER from Blob
az storage blob download \
  --account-name sslprodcerarti \
  --container-name cer-artifacts \
  --name wf_001.cer \
  --file wf_001_downloaded.cer

# Verify the hash matches what's recorded in the workflow state
# The workflow state stores: verification.cer_blob_url (URL to the blob)
# The audit log stores: the SHA-256 of the CSR that was submitted to PKI
```

### Step 3 — Verify the hash chain integrity

```python
# A valid chain means every record's hash_self = SHA-256(record_body + prev_hash)
# If verify_hash_chain returns False, a record was tampered with.
# The Cosmos container is also protected by RBAC (no delete permission on the MI).
```

---

## Data Retention

| Data type | Location | Retention | Method |
|-----------|----------|-----------|--------|
| CER certificates | Blob `cer-artifacts` | 7 years | WORM legal hold (immutability policy) |
| Audit log | Cosmos `audit_log` | 7 years | Cosmos continuous backup + RBAC (no delete) |
| Workflow state | Cosmos `workflow_state` | 7 years | Cosmos continuous backup |
| Application logs | Log Analytics | 2555 days (prod) | Azure Monitor retention policy |
| Approval decisions | Cosmos `audit_log` | 7 years | Included in audit chain |

---

## Data Minimization

The system enforces data minimization at multiple levels:

1. **Private keys:** Never stored anywhere — only in the HSM (enforced by `exportable=False`).
2. **CSR bytes:** Only the SHA-256 hash is stored in Cosmos (full CSR is sent to PKI directly).
3. **CER bytes:** Stored once in WORM Blob; Cosmos stores only the Blob URL.
4. **Approval content:** Only the decision (APPROVED/REJECTED), actor, and timestamp are stored.
5. **PHI/PII:** No patient data is ever present in the SSL renewal system (the certs are for API endpoints, not patient records).

---

## Access Controls

| Role | Access | How enforced |
|------|--------|--------------|
| Agent (Managed Identity) | Read/write workflow_state, audit_log; write Blob; KV cert operations | Azure RBAC (least privilege) |
| SRE | Read-only Cosmos query; no KV key operations | Entra ID group + RBAC |
| Product Director | Approval endpoint (POST /api/approval-callback) only | Adaptive Card token + APIM JWT validation |
| Bob (dev-plane) | Read-only repo; dev MCP only | APIM policy: BOB_DEV_PLANE_TOKEN denied run-plane |
| Auditor | Read-only Cosmos query; read Blob | Entra ID group + Reader role |

---

## Evidence Package (for external audit)

To produce an audit evidence package for a specific renewal:

```bash
# 1. Export audit chain (JSON)
python -m scripts.export_audit_chain --workflow-id wf_001 --output audit_chain.json

# 2. Export workflow state snapshot
python -m scripts.export_workflow_state --workflow-id wf_001 --output workflow_state.json

# 3. Download CER from WORM Blob
az storage blob download \
  --account-name sslprodcerarti \
  --container-name cer-artifacts \
  --name wf_001.cer \
  --file certificate.cer

# 4. Verify chain integrity
python -m scripts.verify_chain --chain-file audit_chain.json
# Expected output: "Chain length: 8, Valid: True, No tampering detected."

# 5. Package for audit submission
zip audit_evidence_wf_001.zip audit_chain.json workflow_state.json certificate.cer
```
