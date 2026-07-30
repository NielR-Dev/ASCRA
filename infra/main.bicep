// infra/main.bicep
// Root Bicep module — composes all sub-modules in dependency order.
//
// Deploy order (enforced by module dependencies):
//   1. Network + Identity
//   2. Data plane (Key Vault, Cosmos, Storage)
//   3. AI runtime (AOAI, AI Foundry)
//   4. APIM + MCP proxy
//   5. Messaging (Service Bus, Event Grid)
//   6. Compute (Function App, Logic Apps)
//   7. Observability (App Insights, Log Analytics)
//
// All secrets are in Key Vault references — no plaintext secrets in this file (G8).

targetScope = 'resourceGroup'

// ---------------------------------------------------------------------------
// Parameters
// ---------------------------------------------------------------------------

@description('Short environment name: dev | uat | prod')
@allowed(['dev', 'uat', 'prod'])
param environmentName string

@description('Azure region for deployment')
param location string = resourceGroup().location

@description('Resource tags applied to all resources')
param tags object = {
  environment: environmentName
  project: 'ssl-renewal-agent'
  managedBy: 'bicep'
}

@description('Name prefix for all resources (e.g. ssl-prod)')
param namePrefix string

// Compute SKU
@description('Azure Functions SKU name')
param functionAppSkuName string = 'EP1'

@description('Azure Functions SKU tier')
param functionAppSkuTier string = 'ElasticPremium'

// Cosmos DB
@description('Cosmos DB capacity mode: Serverless | Provisioned')
@allowed(['Serverless', 'Provisioned'])
param cosmosCapacityMode string = 'Provisioned'

// Key Vault
@description('Key Vault SKU name')
param keyVaultSkuName string = 'standard'

@description('Use Managed HSM (true for uat/prod)')
param keyVaultIsManagedHsm bool = false

// Networking
@description('Enable private endpoints')
param enablePrivateEndpoints bool = true

@description('Enable Azure Firewall (prod only)')
param enableFirewall bool = false

// AOAI
@description('GPT-4o deployment TPM capacity (thousands)')
param openAiDeploymentCapacity int = 30

// Observability
@description('Log retention in days')
param logRetentionDays int = 90

// Agent config
@description('Kill-switch: false disables the orchestrator')
param orchestratorEnabled bool = true

// Mailboxes — passed from param files; real values from Key Vault at runtime
@description('PKI team mailbox address')
param pkiMailbox string = ''

@description('Product Director approver email')
param pdApproverEmail string = ''

// Alert notifications
@description('Email address for operational alerts')
param alertEmailAddress string = ''

// External MCP URLs (APIM-fronted)
@description('Dynatrace MCP backend URL')
param dynatraceMcpUrl string = 'https://dynatrace-mcp.example.com'

@description('Jira MCP backend URL')
param jiraMcpUrl string = 'https://jira-mcp.example.com'

// APIM
@description('APIM SKU (Developer for dev/uat, Premium for prod)')
param apimSkuName string = 'Developer'

@description('Bob dev-plane app ID — denied run-plane access by APIM policy')
param bobDevPlaneAppId string = 'bob-dev-plane'

// ---------------------------------------------------------------------------
// Module 1 — Network
// ---------------------------------------------------------------------------

module network 'modules/network.bicep' = {
  name: 'network'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    enablePrivateEndpoints: enablePrivateEndpoints
    enableFirewall: enableFirewall
  }
}

// ---------------------------------------------------------------------------
// Module 2 — Observability (early, so other modules can reference workspace ID)
// ---------------------------------------------------------------------------

module observability 'modules/appinsights.bicep' = {
  name: 'observability'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    retentionDays: logRetentionDays
    alertEmailAddress: alertEmailAddress
  }
}

// ---------------------------------------------------------------------------
// Module 3 — Azure OpenAI (must exist before Foundry)
// ---------------------------------------------------------------------------

module openai 'modules/openai.bicep' = {
  name: 'openai'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    deploymentName: 'gpt-4o-2024-11-20'
    capacityK: openAiDeploymentCapacity
    agentPrincipalId: identity.outputs.agentIdentityPrincipalId
  }
  dependsOn: [identity]
}

// ---------------------------------------------------------------------------
// Module 4 — Service Bus (needed by identity for role assignment)
// ---------------------------------------------------------------------------

module serviceBus 'modules/servicebus.bicep' = {
  name: 'servicebus'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    skuName: environmentName == 'prod' ? 'Premium' : 'Standard'
    zoneRedundant: environmentName == 'prod'
    enablePrivateEndpoint: enablePrivateEndpoints
    privateEndpointSubnetId: enablePrivateEndpoints ? network.outputs.privateEndpointsSubnetId : ''
  }
}

// ---------------------------------------------------------------------------
// Module 5 — Cosmos DB
// ---------------------------------------------------------------------------

module cosmos 'cosmos.bicep' = {
  name: 'cosmos'
  params: {
    location: location
    accountName: '${namePrefix}-cosmos'
    environment: environmentName
    functionAppPrincipalId: identity.outputs.agentIdentityPrincipalId
  }
  dependsOn: [identity]
}

// ---------------------------------------------------------------------------
// Module 6 — Storage (CER WORM artifacts)
// ---------------------------------------------------------------------------

module storage 'storage.bicep' = {
  name: 'storage'
  params: {
    location: location
    storageAccountName: '${replace(namePrefix, '-', '')}cerarti'
    environment: environmentName
    functionAppPrincipalId: identity.outputs.agentIdentityPrincipalId
  }
  dependsOn: [identity]
}

// ---------------------------------------------------------------------------
// Module 7 — Key Vault / Managed HSM
// ---------------------------------------------------------------------------

module keyVault 'modules/keyvault.bicep' = {
  name: 'keyvault'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    isManagedHsm: keyVaultIsManagedHsm
    skuName: keyVaultSkuName
    agentPrincipalId: identity.outputs.agentIdentityPrincipalId
    enablePrivateEndpoint: enablePrivateEndpoints && !keyVaultIsManagedHsm
    privateEndpointSubnetId: enablePrivateEndpoints ? network.outputs.privateEndpointsSubnetId : ''
    vnetId: network.outputs.vnetId
  }
  dependsOn: [network, identity]
}

// ---------------------------------------------------------------------------
// Module 8 — Identity (depends on Service Bus, Cosmos, Storage, KV IDs)
// Note: circular dependency avoided — identity only needs resource IDs, which
//       are deterministic from naming convention. Role assignments can be deployed
//       after the target resources exist.
// ---------------------------------------------------------------------------

module identity 'modules/identity.bicep' = {
  name: 'identity'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    cosmosAccountId: '/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.DocumentDB/databaseAccounts/${namePrefix}-cosmos'
    keyVaultId: '/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.KeyVault/vaults/${namePrefix}-kv'
    storageAccountId: '/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.Storage/storageAccounts/${replace(namePrefix, '-', '')}cerarti'
    serviceBusNamespaceId: '/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.ServiceBus/namespaces/${namePrefix}-sb'
  }
}

// ---------------------------------------------------------------------------
// Module 9 — AI Foundry Hub + Project
// ---------------------------------------------------------------------------

module foundry 'modules/foundry.bicep' = {
  name: 'foundry'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    aoaiAccountId: openai.outputs.aoaiId
    aoaiEndpoint: openai.outputs.aoaiEndpoint
    aoaiDeploymentName: 'gpt-4o-2024-11-20'
    agentPrincipalId: identity.outputs.agentIdentityPrincipalId
    logAnalyticsWorkspaceId: observability.outputs.logAnalyticsWorkspaceId
  }
  dependsOn: [openai, observability, identity]
}

// ---------------------------------------------------------------------------
// Module 10 — APIM (MCP proxy)
// ---------------------------------------------------------------------------

module apim 'modules/apim.bicep' = {
  name: 'apim'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    skuName: apimSkuName
    vnetSubnetId: apimSkuName == 'Premium' ? network.outputs.apimSubnetId : ''
    agentClientId: identity.outputs.agentIdentityClientId
    bobDevPlaneAppId: bobDevPlaneAppId
    dynatraceMcpUrl: dynatraceMcpUrl
    jiraMcpUrl: jiraMcpUrl
  }
  dependsOn: [network, identity]
}

// ---------------------------------------------------------------------------
// Module 11 — Event Grid (Dynatrace → Service Bus)
// ---------------------------------------------------------------------------

module eventGrid 'modules/eventgrid.bicep' = {
  name: 'eventgrid'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    serviceBusNamespaceId: serviceBus.outputs.serviceBusNamespaceId
    renewalQueueId: '/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.ServiceBus/namespaces/${namePrefix}-sb/queues/ssl-renewals'
    dynatraceWebhookUrl: dynatraceMcpUrl
  }
  dependsOn: [serviceBus]
}

// ---------------------------------------------------------------------------
// Module 12 — Function App (compute)
// ---------------------------------------------------------------------------

module functionApp 'modules/functionapp.bicep' = {
  name: 'functionapp'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    skuName: functionAppSkuName
    skuTier: functionAppSkuTier
    vnetIntegrationSubnetId: network.outputs.functionsSubnetId
    agentIdentityId: identity.outputs.agentIdentityId
    agentIdentityClientId: identity.outputs.agentIdentityClientId
    keyVaultUri: keyVault.outputs.keyVaultUri
    cosmosEndpoint: '/subscriptions/${subscription().subscriptionId}/resourceGroups/${resourceGroup().name}/providers/Microsoft.DocumentDB/databaseAccounts/${namePrefix}-cosmos'
    blobAccountUrl: 'https://${replace(namePrefix, '-', '')}cerarti.blob.${environment().suffixes.storage}'
    foundryProjectEndpoint: foundry.outputs.foundryProjectEndpoint
    aoaiDeploymentName: 'gpt-4o-2024-11-20'
    serviceBusNamespaceFqdn: '${namePrefix}-sb.servicebus.windows.net'
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
    orchestratorEnabled: orchestratorEnabled
  }
  dependsOn: [network, identity, keyVault, cosmos, storage, foundry, serviceBus, observability]
}

// ---------------------------------------------------------------------------
// Module 13 — Logic Apps Standard
// ---------------------------------------------------------------------------

module logicApp 'modules/logicapp.bicep' = {
  name: 'logicapp'
  params: {
    namePrefix: namePrefix
    location: location
    tags: tags
    vnetIntegrationSubnetId: network.outputs.functionsSubnetId
    agentIdentityId: identity.outputs.agentIdentityId
    agentIdentityClientId: identity.outputs.agentIdentityClientId
    appInsightsConnectionString: observability.outputs.appInsightsConnectionString
  }
  dependsOn: [network, identity, observability]
}

// ---------------------------------------------------------------------------
// Outputs — referenced by deploy.yml post-deploy steps and other scripts
// ---------------------------------------------------------------------------

output functionAppName string = functionApp.outputs.functionAppName
output functionAppHostname string = functionApp.outputs.functionAppHostname
output keyVaultUri string = keyVault.outputs.keyVaultUri
output foundryProjectEndpoint string = foundry.outputs.foundryProjectEndpoint
output apimGatewayUrl string = apim.outputs.apimGatewayUrl
output appInsightsConnectionString string = observability.outputs.appInsightsConnectionString
output agentIdentityClientId string = identity.outputs.agentIdentityClientId
