# Disaster Recovery Guide — SSL Certificate Renewal Agent

**RPO:** 1 hour (Cosmos DB continuous backup)  
**RTO:** 4 hours (full failover to DR region)  
**DR Region:** `westus2` (primary: `eastus`)

---

## Cosmos DB Point-In-Time Restore (PITR)

Cosmos DB is configured with continuous backup (30-day restore window). To restore:

```bash
# Find the last good timestamp (e.g., before a bad data write)
# Check audit logs for the last known-good operation timestamp
RESTORE_TIMESTAMP="2024-01-15T10:30:00Z"

# Restore to a new account (never overwrite production)
az cosmosdb restore \
  --account-name ssl-prod-cosmos \
  --target-database-account-name ssl-prod-cosmos-restored \
  --restore-timestamp "$RESTORE_TIMESTAMP" \
  --location eastus

# After verification, update COSMOS_ENDPOINT in Key Vault to point to restored account
az keyvault secret set \
  --vault-name ssl-prod-hsm \
  --name cosmos-endpoint \
  --value "https://ssl-prod-cosmos-restored.documents.azure.com:443/"

# Restart Function App to pick up new endpoint
az functionapp restart --name ssl-prod-func --resource-group ssl-renewal-rg-prod
```

---

## Key Vault Recovery (Soft-Delete + Purge Protection)

Keys and secrets are protected by 90-day soft-delete and purge protection.

```bash
# List deleted secrets
az keyvault secret list-deleted --vault-name ssl-prod-hsm

# Recover a deleted secret
az keyvault secret recover \
  --vault-name ssl-prod-hsm \
  --name pki-mailbox

# Recover a deleted managed HSM key
az keyvault key recover \
  --vault-name ssl-prod-hsm \
  --name <key-name>
```

> **Note:** Purge protection means keys cannot be permanently deleted for 90 days.
> This is a compliance requirement — do not attempt to bypass it.

---

## DR Region Failover

In the event of a regional outage (`eastus`):

### 1. Validate DR infrastructure is ready

```bash
# DR infra should be pre-deployed but idle (orchestrator_enabled=false)
az deployment group show \
  --resource-group ssl-renewal-rg-prod-dr \
  --name latest-dr-deploy
```

### 2. Point DNS/Traffic Manager to DR region

```bash
az network traffic-manager endpoint update \
  --profile-name ssl-renewal-tm \
  --name prod-endpoint \
  --type AzureEndpoints \
  --endpoint-status Disabled

az network traffic-manager endpoint update \
  --profile-name ssl-renewal-tm \
  --name dr-endpoint \
  --type AzureEndpoints \
  --endpoint-status Enabled
```

### 3. Enable orchestrator in DR region

```bash
az keyvault secret set \
  --vault-name ssl-dr-hsm \
  --name orchestrator-enabled \
  --value true
```

### 4. Restore Cosmos data to DR region

```bash
# Cosmos DB geo-redundant backup is pre-configured in prod.bicepparam
# The DR account is updated via continuous replication (< 15 min lag)
# Verify the DR Cosmos account has recent data
az cosmosdb show \
  --name ssl-dr-cosmos \
  --resource-group ssl-renewal-rg-prod-dr \
  --query "writeLocations"
```

---

## Blob Storage Recovery (CER WORM artifacts)

CER files are stored with 7-year legal hold and versioning enabled.
WORM blobs cannot be deleted or overwritten — recovery means ensuring the
correct version is referenced in Cosmos (`verification.cer_blob_url`).

```bash
# List blob versions for a specific workflow's CER
az storage blob list \
  --account-name sslprodcerarti \
  --container-name cer-artifacts \
  --include v \
  --prefix "wf_001" \
  --query "[].{name:name,version:versionId,created:properties.creationTime}"
```

---

## Service Bus Message Recovery (Dead-Letter)

Messages in the dead-letter queue can be replayed manually:

```bash
# List dead-lettered messages
az servicebus queue show \
  --name ssl-renewals \
  --namespace-name ssl-prod-sb \
  --resource-group ssl-renewal-rg-prod \
  --query deadLetterMessageCount

# Use the Azure Service Bus Explorer or SDK to peek and replay
# python -m scripts.replay_dlq --namespace ssl-prod-sb --queue ssl-renewals
```

---

## RTO/RPO Summary

| Component | RPO | RTO | Method |
|-----------|-----|-----|--------|
| Cosmos DB workflow state | 1 hour | 30 min | PITR restore or geo-replication |
| Cosmos DB audit log | 1 hour | 30 min | PITR restore (append-only — no data loss) |
| Key Vault keys | 0 (geo-replicated) | 15 min | Automatic geo-replication |
| Blob CER artifacts | 0 (WORM) | 5 min | WORM read from same/DR region |
| Function App | 0 (stateless) | 15 min | Redeploy from GitHub to DR region |
| Service Bus messages | 30 min | 10 min | Geo-secondary namespace |
