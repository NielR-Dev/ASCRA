using './main.bicep'

param environmentName = 'dev'
param location = 'eastus'
param tags = {
  environment: 'dev'
  project: 'ssl-renewal-agent'
  managedBy: 'bicep'
}

// Resource naming (dev prefix avoids conflicts with uat/prod)
param namePrefix = 'ssl-dev'

// Compute SKUs — smallest viable for dev
param functionAppSkuName = 'Y1'   // Consumption plan
param functionAppSkuTier = 'Dynamic'

// Cosmos DB — dev uses serverless (cheapest)
param cosmosCapacityMode = 'Serverless'

// Key Vault — standard (not Managed HSM for dev cost savings; HSM in UAT/prod)
param keyVaultSkuName = 'standard'
param keyVaultIsManagedHsm = false

// Networking — no private endpoints in dev (saves cost)
param enablePrivateEndpoints = false
param enableFirewall = false

// AOAI
param openAiDeploymentCapacity = 10   // TPM thousands

// Observability — reduced retention
param logRetentionDays = 30

// Kill-switch — on (enabled) in dev
param orchestratorEnabled = true

// PKI/approval stubs — dev points to test mailboxes
param pkiMailbox = 'pki-dev@example.com'
param pdApproverEmail = 'pd-dev@example.com'
