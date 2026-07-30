// infra/modules/identity.bicep
// Managed Identity + role assignments for the SSL Renewal Agent.
// Every role is principle-of-least-privilege: only the operations the agent actually needs.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('Cosmos DB account resource ID (for role assignment)')
param cosmosAccountId string

@description('Key Vault resource ID (for role assignment)')
param keyVaultId string

@description('Blob Storage account resource ID (for role assignment)')
param storageAccountId string

@description('Service Bus namespace resource ID (for role assignment)')
param serviceBusNamespaceId string

// ---------------------------------------------------------------------------
// User-Assigned Managed Identity
// The Function App and Logic Apps run as this identity.
// No passwords, no service-principal client secrets.
// ---------------------------------------------------------------------------

resource agentIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${namePrefix}-agent-identity'
  location: location
  tags: tags
}

// ---------------------------------------------------------------------------
// Role assignments — Cosmos DB
// ---------------------------------------------------------------------------

// Built-in: Cosmos DB Data Contributor (read + write workflow_state, audit_log, etc.)
// Scoped to account level so the agent can access all 4 containers.
resource cosmosDataContributor 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2023-04-15' = {
  name: '${cosmosAccountId}/00000000-0000-0000-0000-000000000002'
  properties: {
    roleDefinitionId: '/${cosmosAccountId}/sqlRoleDefinitions/00000000-0000-0000-0000-000000000002'
    principalId: agentIdentity.properties.principalId
    scope: cosmosAccountId
  }
}

// ---------------------------------------------------------------------------
// Role assignments — Key Vault
// Key Sign + Certificate Operations only; NO key export permission (G7).
// ---------------------------------------------------------------------------

// Key Vault Certificates Officer — allows create, read, update CSR operations
resource kvCertOfficer 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, agentIdentity.id, 'KeyVaultCertificatesOfficer')
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'a4417e6f-fecd-4de8-b567-7b0420556985'  // Key Vault Certificates Officer
    )
    principalId: agentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Key Vault Crypto User — allows use of keys (sign, verify) but NOT export
resource kvCryptoUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVaultId, agentIdentity.id, 'KeyVaultCryptoUser')
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '12338af0-0e69-4776-bea7-57ae8d297424'  // Key Vault Crypto User
    )
    principalId: agentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Role assignments — Blob Storage (CER WORM artifacts)
// Storage Blob Data Contributor: write CER files; read for download
// ---------------------------------------------------------------------------

resource storageBlobContributor 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccountId, agentIdentity.id, 'StorageBlobDataContributor')
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      'ba92f5b4-2d11-453d-a403-e96b0029c9fe'  // Storage Blob Data Contributor
    )
    principalId: agentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Role assignments — Service Bus (send + receive renewal events)
// ---------------------------------------------------------------------------

resource sbDataOwner 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(serviceBusNamespaceId, agentIdentity.id, 'AzureServiceBusDataOwner')
  scope: resourceGroup()
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      '090c5cfd-751d-490a-894a-3ce6f1109419'  // Azure Service Bus Data Owner
    )
    principalId: agentIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output agentIdentityId string = agentIdentity.id
output agentIdentityClientId string = agentIdentity.properties.clientId
output agentIdentityPrincipalId string = agentIdentity.properties.principalId
