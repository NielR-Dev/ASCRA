# Phase 11 — Cloud Deployment (IaC)

> **Pre-read:** [00-context.md](00-context.md) · depends on P4, P5, P6, P10
> **Deliverable:** Complete Bicep module set, rollout order, networking
> **Task IDs:** T16
> **Effort estimate:** ~6 person-days

---

## Your Task

Implement the full Infrastructure-as-Code (Bicep) module set. Every resource must be deployed through Bicep — no portal-only configuration. Security settings (HSM, WORM, private endpoints, PITR) are non-negotiable.

---

## What to Produce

```
infra/
├── main.bicep              # root module: composes all modules
├── prod.bicepparam         # prod parameters
├── uat.bicepparam          # uat parameters
├── dev.bicepparam          # dev parameters
├── modules/
│   ├── identity.bicep      # Managed Identity + role assignments
│   ├── foundry.bicep       # AI Foundry project + agent
│   ├── openai.bicep        # AOAI + gpt-4o deployment
│   ├── keyvault.bicep      # Managed HSM, non-exportable key policy
│   ├── cosmos.bicep        # DB + 4 containers, PITR
│   ├── storage.bicep       # Blob WORM/legal-hold
│   ├── functionapp.bicep   # Functions (Python isolated)
│   ├── logicapp.bicep      # Logic Apps Standard
│   ├── apim.bicep          # APIM in MCP mode with JWT policy
│   ├── servicebus.bicep    # Service Bus namespace + queue
│   ├── eventgrid.bicep     # Event Grid subscription (Dynatrace webhook)
│   ├── appinsights.bicep   # App Insights + Log Analytics workspace
│   └── network.bicep       # Hub-spoke VNet, private endpoints, Firewall
```

---

## Rollout Order (deploy in this exact sequence)

The modules have dependencies — deploy in dependency order to avoid missing references:

1. **Network + identity** — VNet, private DNS zones, Managed Identity (`identity.bicep`, `network.bicep`)
2. **Data plane** — Key Vault (HSM) + Cosmos DB + Storage, all with private endpoints (`keyvault.bicep`, `cosmos.bicep`, `storage.bicep`)
3. **AI runtime** — AI Foundry project + AOAI gpt-4o deployment (`foundry.bicep`, `openai.bicep`)
4. **APIM + external MCP** — APIM in MCP mode; register external MCP servers; JWT validation policy (`apim.bicep`)
5. **Messaging** — Service Bus namespace + queue; Event Grid subscription to Dynatrace webhook (`servicebus.bicep`, `eventgrid.bicep`)
6. **Compute** — Function App + Logic Apps (deploy code + definitions after Bicep) (`functionapp.bicep`, `logicapp.bicep`)
7. **Conversational** — Copilot Studio topics + Adaptive Cards + approval callback wiring (manual import into Copilot Studio environment)
8. **Observability** — App Insights, Log Analytics, Purview, Defender/Sentinel; configure alert rules (`appinsights.bicep`)

---

## Critical Resource Settings

### Key Vault (Managed HSM) — `keyvault.bicep`

```bicep
resource keyVault 'Microsoft.KeyVault/managedHSMs@2023-07-01' = {
  properties: {
    // HSM SKU for FIPS 140-2 Level 3 + non-exportable key guarantee
    sku: { family: 'B', name: 'Standard_B1' }
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true        // prevents accidental/malicious deletion
    // publicNetworkAccess: 'Disabled'  // private endpoint only
  }
}
// Key policy: only Key Sign + Certificate Create; NO Export permission on the MI
```

### Cosmos DB — `cosmos.bicep`

```bicep
// Continuous backup (PITR) with 30-day restore window
// Zone-redundant: isZoneRedundant: true
// 4 containers: workflow_state, audit_log, idempotency, batch
// Indexing: exclude large string paths; include state, cn, updated_at, batch_id
// TTL: only on idempotency container (30 days); all others: no TTL
// Public network access: Disabled; private endpoint only
```

### Blob Storage (WORM) — `storage.bicep`

```bicep
// cer-artifacts container:
//   - isVersioningEnabled: true
//   - immutabilityPolicy: { immutabilityPeriodSinceCreationInDays: 2555 }  // 7 years
//   - enableLegalHold: true (applied after upload)
//   - publicAccess: 'None'
```

### APIM — `apim.bicep`

```bicep
// MCP mode enabled
// JWT validation policy on all APIs: require valid Entra token
// Bob's app registration: explicitly excluded from all run-plane API products
// Throttling: PKI_RATE_PER_MIN, JIRA_RATE_PER_MIN, SNOW_RATE_PER_MIN per product
// Private endpoint only; no public APIM gateway
```

---

## Role Assignments (implement in `identity.bicep`)

All permissions via Managed Identity RBAC — no service principals with passwords.

| Resource | MI Permission | Scope |
|----------|--------------|-------|
| Key Vault (HSM) | `Key Sign` + `Certificate Create` | Specific vault only |
| Key Vault (HSM) | **NOT** `Key Export` | Explicitly excluded |
| Cosmos DB | `Cosmos DB Built-in Data Contributor` | `workflow_state`, `audit_log`, `idempotency`, `batch` containers |
| Blob | `Storage Blob Data Contributor` | `cer-artifacts` container only |
| Service Bus | `Azure Service Bus Data Sender` + `Data Receiver` | Specific queue |
| Graph (app registration) | `Mail.Send`, `Mail.Read.Shared` | Shared mailbox scope only |
| **Bob's app registration** | **Denied** | All run-plane APIM products |

---

## Networking — `network.bicep`

```
Hub VNet (shared services)
  └── Spoke VNet (ssl-renewal)
        ├── Private Endpoints:
        │   ├── Key Vault (HSM)
        │   ├── Cosmos DB
        │   ├── Storage (Blob)
        │   ├── Service Bus
        │   ├── AI Foundry
        │   └── APIM (internal mode)
        └── Azure Firewall FQDN allow-list (egress only):
            ├── jira.test-domain.com (Jira SaaS)
            ├── *.dynatrace.com (Dynatrace API)
            └── smtp.test-domain.com (PKI mailbox relay — only if direct SMTP needed)
```

No public inbound except the Event Grid/webhook ingress (secured by subscription validation + APIM JWT).

---

## Acceptance Criteria

- `az deployment group validate` succeeds with no errors
- `az deployment group what-if` shows the full topology with no unexpected destructive changes
- Key Vault HSM keys are `exportable=False` (verifiable with `az keyvault key show`)
- No public-facing data-plane endpoints (Cosmos, Blob, Key Vault — private endpoints only)
- Blob `cer-artifacts` has WORM/legal-hold enabled
- Cosmos has PITR enabled
- Bob's app registration is explicitly excluded from all run-plane APIM products
- All role assignments are least-privilege (no `Owner`, `Contributor`, or broad data-plane roles)

---

## Verification

```bash
# Validate
az deployment group validate -g ssl-renewal-rg-dev \
  --template-file infra/main.bicep \
  --parameters @infra/dev.bicepparam

# What-if
az deployment group what-if -g ssl-renewal-rg-prod \
  --template-file infra/main.bicep \
  --parameters @infra/prod.bicepparam

# Post-deploy assertions (add to deploy.yml as a verification step)
az keyvault certificate show --vault-name kv-ssl-hsm --name test-cert --query "policy.keyProperties.exportable"
# Must return: false

az cosmosdb show --name cosmos-ssl-renewal --resource-group ssl-renewal-rg-prod \
  --query "backupPolicy.type"
# Must return: "Continuous"
```
