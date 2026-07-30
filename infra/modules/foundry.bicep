// infra/modules/foundry.bicep
// Azure AI Foundry Hub + Project for the SSL Renewal orchestrator agent.
// The agent (MAF 1.0 / Foundry Agent Service) is deployed within this project.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('Azure OpenAI account resource ID (connected resource)')
param aoaiAccountId string

@description('Azure OpenAI endpoint')
param aoaiEndpoint string

@description('GPT-4o deployment name')
param aoaiDeploymentName string

@description('Agent Managed Identity principal ID')
param agentPrincipalId string

@description('Log Analytics workspace resource ID for diagnostics')
param logAnalyticsWorkspaceId string = ''

// ---------------------------------------------------------------------------
// AI Foundry Hub
// ---------------------------------------------------------------------------

resource foundryHub 'Microsoft.MachineLearningServices/workspaces@2024-04-01' = {
  name: '${namePrefix}-hub'
  location: location
  tags: tags
  kind: 'Hub'
  sku: {
    name: 'Basic'
    tier: 'Basic'
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: '${namePrefix} SSL Renewal Hub'
    description: 'AI Foundry Hub for the Autonomous SSL Certificate Renewal Agent'
    publicNetworkAccess: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// AI Foundry Project
// All agent threads, tool calls, and eval runs live inside this project.
// ---------------------------------------------------------------------------

resource foundryProject 'Microsoft.MachineLearningServices/workspaces@2024-04-01' = {
  name: '${namePrefix}-project'
  location: location
  tags: tags
  kind: 'Project'
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    friendlyName: '${namePrefix} SSL Renewal Agent'
    description: 'Foundry Project for the SSL Certificate Renewal orchestrator'
    hubResourceId: foundryHub.id
    publicNetworkAccess: 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Diagnostic settings
// ---------------------------------------------------------------------------

resource diagnostics 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = if (!empty(logAnalyticsWorkspaceId)) {
  name: '${namePrefix}-foundry-diag'
  scope: foundryProject
  properties: {
    workspaceId: logAnalyticsWorkspaceId
    logs: [
      {
        categoryGroup: 'allLogs'
        enabled: true
        retentionPolicy: { enabled: false, days: 0 }
      }
    ]
    metrics: [
      {
        category: 'AllMetrics'
        enabled: true
        retentionPolicy: { enabled: false, days: 0 }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output foundryHubId string = foundryHub.id
output foundryProjectId string = foundryProject.id
output foundryProjectEndpoint string = 'https://${foundryProject.name}.${location}.api.azureml.ms'
