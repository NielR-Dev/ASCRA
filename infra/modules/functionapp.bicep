// infra/modules/functionapp.bicep
// Azure Functions (Python 3.11 isolated process) hosting the SSL Renewal Agent.
// Elastic Premium plan for warm instances; VNet integration for private endpoint access.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('Function App SKU name (Y1=Consumption, EP1/EP2=ElasticPremium)')
param skuName string = 'EP1'

@description('Function App SKU tier')
param skuTier string = 'ElasticPremium'

@description('VNet integration subnet ID')
param vnetIntegrationSubnetId string

@description('Agent Managed Identity resource ID')
param agentIdentityId string

@description('Agent Managed Identity client ID')
param agentIdentityClientId string

@description('Key Vault URI')
param keyVaultUri string

@description('Cosmos DB endpoint')
param cosmosEndpoint string

@description('Blob Storage account URL')
param blobAccountUrl string

@description('AI Foundry project endpoint')
param foundryProjectEndpoint string

@description('Azure OpenAI deployment name')
param aoaiDeploymentName string = 'gpt-4o-2024-11-20'

@description('Service Bus namespace FQDN')
param serviceBusNamespaceFqdn string

@description('App Insights connection string (optional)')
param appInsightsConnectionString string = ''

@description('Orchestrator enabled (kill-switch)')
param orchestratorEnabled bool = true

// ---------------------------------------------------------------------------
// Storage account for Azure Functions (required by the runtime)
// This is NOT the CER WORM storage — it's the Functions internal store.
// ---------------------------------------------------------------------------

resource funcStorage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: '${replace(namePrefix, '-', '')}funcstor'
  location: location
  tags: tags
  sku: {
    name: 'Standard_LRS'
  }
  kind: 'StorageV2'
  properties: {
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    supportsHttpsTrafficOnly: true
  }
}

// ---------------------------------------------------------------------------
// App Service Plan
// ---------------------------------------------------------------------------

resource appServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: '${namePrefix}-asp'
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuTier
  }
  kind: 'elastic'
  properties: {
    reserved: true   // Linux
    maximumElasticWorkerCount: 20
  }
}

// ---------------------------------------------------------------------------
// Function App
// ---------------------------------------------------------------------------

resource functionApp 'Microsoft.Web/sites@2023-01-01' = {
  name: '${namePrefix}-func'
  location: location
  tags: tags
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${agentIdentityId}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    reserved: true
    virtualNetworkSubnetId: vnetIntegrationSubnetId
    httpsOnly: true
    siteConfig: {
      pythonVersion: '3.11'
      linuxFxVersion: 'Python|3.11'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      appSettings: [
        // Identity — use user-assigned MI (G8: no client secret)
        { name: 'AZURE_CLIENT_ID', value: agentIdentityClientId }

        // Function runtime
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${funcStorage.name};AccountKey=${funcStorage.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }

        // Agent config — all from KV references or direct env vars (no secrets in code, G8)
        { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
        { name: 'AZURE_OPENAI_DEPLOYMENT', value: aoaiDeploymentName }
        { name: 'KEY_VAULT_URI', value: keyVaultUri }
        { name: 'COSMOS_ENDPOINT', value: cosmosEndpoint }
        { name: 'BLOB_ACCOUNT_URL', value: blobAccountUrl }
        { name: 'ORCHESTRATOR_ENABLED', value: string(orchestratorEnabled) }

        // Service Bus connection (Managed Identity — no connection string with key)
        { name: 'SERVICE_BUS_NAMESPACE_FQDN', value: serviceBusNamespaceFqdn }

        // Observability
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        { name: 'PYTHON_ENABLE_WORKER_EXTENSIONS', value: '1' }

        // Secrets loaded via Key Vault references at runtime — NOT stored here
        { name: 'PKI_MAILBOX', value: '@Microsoft.KeyVault(VaultName=${split(keyVaultUri, '.')[0]};SecretName=pki-mailbox)' }
        { name: 'PD_APPROVER_EMAIL', value: '@Microsoft.KeyVault(VaultName=${split(keyVaultUri, '.')[0]};SecretName=pd-approver-email)' }
        { name: 'SLACK_SIGNING_SECRET', value: '@Microsoft.KeyVault(VaultName=${split(keyVaultUri, '.')[0]};SecretName=slack-signing-secret)' }
      ]
      cors: {
        allowedOrigins: ['https://portal.azure.com']
        supportCredentials: false
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output functionAppId string = functionApp.id
output functionAppName string = functionApp.name
output functionAppHostname string = functionApp.properties.defaultHostName
