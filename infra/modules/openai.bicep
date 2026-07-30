// infra/modules/openai.bicep
// Azure OpenAI Service account + gpt-4o deployment.
// The orchestrator uses this for all LLM calls.
// No Responsible AI bypass — content safety is left at default for text workloads.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources — AOAI availability varies by region')
param location string = 'eastus'

@description('Resource tags')
param tags object

@description('GPT-4o model deployment name')
param deploymentName string = 'gpt-4o-2024-11-20'

@description('TPM capacity (thousands) for the gpt-4o deployment')
param capacityK int = 30

@description('Agent Managed Identity principal ID (granted Cognitive Services User)')
param agentPrincipalId string

// ---------------------------------------------------------------------------
// AOAI account
// ---------------------------------------------------------------------------

resource aoai 'Microsoft.CognitiveServices/accounts@2023-10-01-preview' = {
  name: '${namePrefix}-aoai'
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: '${namePrefix}-aoai'
    publicNetworkAccess: 'Enabled'
    disableLocalAuth: true    // Entra ID auth only — no API key access (G8)
    networkAcls: {
      defaultAction: 'Allow'
    }
  }
  identity: {
    type: 'SystemAssigned'
  }
}

// ---------------------------------------------------------------------------
// GPT-4o deployment
// ---------------------------------------------------------------------------

resource gpt4oDeployment 'Microsoft.CognitiveServices/accounts/deployments@2023-10-01-preview' = {
  parent: aoai
  name: deploymentName
  sku: {
    name: 'Standard'
    capacity: capacityK
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4o'
      version: '2024-11-20'
    }
    versionUpgradeOption: 'OnceCurrentVersionExpired'
    raiPolicyName: 'Microsoft.Default'
  }
}

// ---------------------------------------------------------------------------
// Role assignment — Cognitive Services OpenAI User
// Allows the agent identity to call the AOAI API via Entra ID token.
// ---------------------------------------------------------------------------

resource aoaiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aoai.id, agentPrincipalId, 'CognitiveServicesOpenAIUser')
  scope: aoai
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'  // Cognitive Services OpenAI User
    )
    principalId: agentPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output aoaiId string = aoai.id
output aoaiEndpoint string = aoai.properties.endpoint
output deploymentName string = gpt4oDeployment.name
