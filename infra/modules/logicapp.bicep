// infra/modules/logicapp.bicep
// Logic App Standard for certificate-expired notifications and PKI email dispatch.
// Uses system-assigned managed identity — no passwords or connection strings in definitions.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('VNet integration subnet ID')
param vnetIntegrationSubnetId string = ''

@description('Agent Managed Identity resource ID')
param agentIdentityId string = ''

@description('Agent Managed Identity client ID')
param agentIdentityClientId string = ''

@description('App Insights connection string')
param appInsightsConnectionString string = ''

// ---------------------------------------------------------------------------
// Storage account for Logic App Standard runtime
// ---------------------------------------------------------------------------

resource laStorage 'Microsoft.Storage/storageAccounts@2023-01-01' = {
  name: '${replace(namePrefix, '-', '')}lastor'
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
// App Service Plan — WS1 for Logic Apps Standard
// ---------------------------------------------------------------------------

resource laAppServicePlan 'Microsoft.Web/serverfarms@2023-01-01' = {
  name: '${namePrefix}-la-asp'
  location: location
  tags: tags
  sku: {
    name: 'WS1'
    tier: 'WorkflowStandard'
  }
  kind: 'elastic'
  properties: {
    reserved: false
    maximumElasticWorkerCount: 3
  }
}

// ---------------------------------------------------------------------------
// Logic App Standard site
// ---------------------------------------------------------------------------

resource logicApp 'Microsoft.Web/sites@2023-01-01' = {
  name: '${namePrefix}-la'
  location: location
  tags: tags
  kind: 'functionapp,workflowapp'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: !empty(agentIdentityId) ? {
      '${agentIdentityId}': {}
    } : {}
  }
  properties: {
    serverFarmId: laAppServicePlan.id
    virtualNetworkSubnetId: !empty(vnetIntegrationSubnetId) ? vnetIntegrationSubnetId : null
    httpsOnly: true
    siteConfig: {
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      appSettings: [
        {
          name: 'AzureWebJobsStorage'
          value: 'DefaultEndpointsProtocol=https;AccountName=${laStorage.name};AccountKey=${laStorage.listKeys().keys[0].value};EndpointSuffix=core.windows.net'
        }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'node' }
        { name: 'APP_KIND', value: 'workflowApp' }
        { name: 'WEBSITE_NODE_DEFAULT_VERSION', value: '~18' }
        { name: 'WORKFLOWS_SUBSCRIPTION_ID', value: subscription().subscriptionId }
        { name: 'WORKFLOWS_RESOURCE_GROUP_NAME', value: resourceGroup().name }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsightsConnectionString }
        {
          name: 'MANAGED_IDENTITY_CLIENT_ID'
          value: agentIdentityClientId
        }
      ]
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output logicAppId string = logicApp.id
output logicAppName string = logicApp.name
output logicAppHostname string = logicApp.properties.defaultHostName
