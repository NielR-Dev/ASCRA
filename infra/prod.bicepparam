using './main.bicep'

param environmentName = 'prod'
param location = 'eastus'
param tags = {
  environment: 'prod'
  project: 'ssl-renewal-agent'
  managedBy: 'bicep'
  dataClassification: 'confidential'
  complianceScope: 'healthcare'
}

param namePrefix = 'ssl-prod'

// Compute — Premium Elastic Plan for warm instances
param functionAppSkuName = 'EP2'
param functionAppSkuTier = 'ElasticPremium'

// Cosmos DB — provisioned with zone-redundancy
param cosmosCapacityMode = 'Provisioned'

// Key Vault — Managed HSM (FIPS 140-2 Level 3) — mandatory for G7
param keyVaultSkuName = 'Standard_B1'
param keyVaultIsManagedHsm = true

// Networking — full private endpoint + Firewall coverage
param enablePrivateEndpoints = true
param enableFirewall = true

param openAiDeploymentCapacity = 120   // Full TPM for production load

// 7-year log retention for audit compliance (healthcare)
param logRetentionDays = 2555   // 7 × 365

param orchestratorEnabled = true   // Set to false via KV reference for kill-switch

// Production mailboxes — injected at deploy time from Key Vault references
// These values are PLACEHOLDERS; actual values come from the param-file secret override
// at deployment time via: --parameters pkiMailbox="$(az keyvault secret show ...)"
param pkiMailbox = 'pki@example.com'
param pdApproverEmail = 'pd@example.com'
