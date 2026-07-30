// infra/modules/keyvault.bicep
// Azure Key Vault (Standard) or Managed HSM for production.
// FIPS 140-2 Level 3 for Managed HSM — non-exportable key guarantee (G7).
// Soft-delete + purge protection: keys cannot be accidentally deleted.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('Use Managed HSM (true in uat/prod; false in dev for cost savings)')
param isManagedHsm bool = false

@description('SKU name — "standard"/"premium" for Key Vault; "Standard_B1" for Managed HSM')
param skuName string = 'standard'

@description('Tenant ID for Key Vault access policies')
param tenantId string = tenant().tenantId

@description('Agent Managed Identity principal ID (granted certificate + crypto operations)')
param agentPrincipalId string

@description('Enable private endpoint for the Key Vault')
param enablePrivateEndpoint bool = false

@description('Private endpoint subnet ID')
param privateEndpointSubnetId string = ''

@description('VNet ID for private DNS zone link')
param vnetId string = ''

// ---------------------------------------------------------------------------
// Standard Key Vault (dev / cost-saving mode)
// ---------------------------------------------------------------------------

resource keyVault 'Microsoft.KeyVault/vaults@2023-07-01' = if (!isManagedHsm) {
  name: '${namePrefix}-kv'
  location: location
  tags: tags
  properties: {
    sku: {
      family: 'A'
      name: skuName
    }
    tenantId: tenantId
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true          // G7: key cannot be purged/exported
    enableRbacAuthorization: true        // use Azure RBAC, not legacy access policies
    publicNetworkAccess: enablePrivateEndpoint ? 'Disabled' : 'Enabled'
    networkAcls: enablePrivateEndpoint ? {
      defaultAction: 'Deny'
      bypass: 'AzureServices'
    } : null
  }
}

// ---------------------------------------------------------------------------
// Managed HSM (uat / prod) — FIPS 140-2 Level 3
// ---------------------------------------------------------------------------

resource managedHsm 'Microsoft.KeyVault/managedHSMs@2023-07-01' = if (isManagedHsm) {
  name: '${namePrefix}-hsm'
  location: location
  tags: tags
  sku: {
    family: 'B'
    name: 'Standard_B1'
  }
  properties: {
    tenantId: tenantId
    initialAdminObjectIds: [agentPrincipalId]
    enableSoftDelete: true
    softDeleteRetentionInDays: 90
    enablePurgeProtection: true          // G7: cannot purge HSM keys
    publicNetworkAccess: 'Disabled'
  }
}

// ---------------------------------------------------------------------------
// Private endpoint (uat/prod)
// ---------------------------------------------------------------------------

resource kvPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-09-01' = if (enablePrivateEndpoint && !isManagedHsm) {
  name: '${namePrefix}-kv-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${namePrefix}-kv-pe-conn'
        properties: {
          privateLinkServiceId: keyVault.id
          groupIds: ['vault']
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output keyVaultId string = isManagedHsm ? managedHsm.id : keyVault.id
output keyVaultUri string = isManagedHsm ? managedHsm.properties.hsmUri : keyVault.properties.vaultUri
