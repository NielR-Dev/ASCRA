using './main.bicep'

param environmentName = 'uat'
param location = 'eastus'
param tags = {
  environment: 'uat'
  project: 'ssl-renewal-agent'
  managedBy: 'bicep'
}

param namePrefix = 'ssl-uat'

// Compute — App Service Plan P0v3 (production-like)
param functionAppSkuName = 'EP1'
param functionAppSkuTier = 'ElasticPremium'

// Cosmos DB — provisioned throughput (closer to prod behaviour)
param cosmosCapacityMode = 'Provisioned'

// Key Vault — Managed HSM in UAT to test HSM path
param keyVaultSkuName = 'Standard_B1'
param keyVaultIsManagedHsm = true

// Networking — private endpoints enabled (mirrors prod)
param enablePrivateEndpoints = true
param enableFirewall = false

param openAiDeploymentCapacity = 30

param logRetentionDays = 60

param orchestratorEnabled = true

// UAT uses sandbox mailboxes
param pkiMailbox = 'pki-sandbox@example.com'
param pdApproverEmail = 'pd-uat@example.com'
