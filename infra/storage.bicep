// infra/storage.bicep
// Azure Blob Storage — cer-artifacts container with WORM / legal hold.
//
// Compliance:
//   - 7-year legal hold (2555 days) on cer-artifacts container.
//   - Versioning enabled so any overwrite attempt creates a new version (not a replacement).
//   - Soft-delete enabled (30-day recovery window) in addition to WORM.
//   - Public access disabled; all access via Private Endpoint (network.bicep).
//   - No anonymous access; all access via Managed Identity (G8).

@description('Azure region for deployment')
param location string = resourceGroup().location

@description('Storage account name (globally unique; 3–24 lowercase alphanumeric)')
param storageAccountName string

@description('Environment tag')
param environment string = 'prod'

@description('Managed Identity principal ID of the Function App')
param functionAppPrincipalId string

// ---------------------------------------------------------------------------
// Storage Account
// ---------------------------------------------------------------------------
resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: storageAccountName
  location: location
  kind: 'StorageV2'
  sku: {
    name: 'Standard_ZRS'  // Zone-redundant storage for HA
  }
  tags: {
    environment: environment
    system: 'ssl-renewal-agent'
    data_classification: 'confidential'
    retention: '7-years-worm'
  }
  properties: {
    accessTier: 'Hot'
    allowBlobPublicAccess: false       // No anonymous access
    allowSharedKeyAccess: false        // Managed Identity only (G8 — no storage account keys)
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    publicNetworkAccess: 'Disabled'    // Private Endpoint only
    encryption: {
      services: {
        blob: {
          enabled: true
          keyType: 'Account'
        }
      }
      keySource: 'Microsoft.Storage'   // Service-managed keys (CMK option: see G8 follow-up)
    }
  }
}

// ---------------------------------------------------------------------------
// Blob Service — enable versioning and soft-delete
// ---------------------------------------------------------------------------
resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
  properties: {
    isVersioningEnabled: true          // Required for WORM + legal hold
    deleteRetentionPolicy: {
      enabled: true
      days: 30                         // Soft-delete retention: 30 days
      allowPermanentDelete: false      // Cannot permanently delete during soft-delete window
    }
    containerDeleteRetentionPolicy: {
      enabled: true
      days: 30
    }
  }
}

// ---------------------------------------------------------------------------
// cer-artifacts Container
// ---------------------------------------------------------------------------
resource cerArtifactsContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'cer-artifacts'
  properties: {
    publicAccess: 'None'               // No anonymous access
    immutableStorageWithVersioning: {
      enabled: true                    // WORM: requires versioning to be enabled
    }
    metadata: {
      purpose: 'ssl-renewal-cer-files'
      retention: '7-years'
    }
  }
}

// ---------------------------------------------------------------------------
// Immutability Policy on cer-artifacts — 7-year WORM
// ---------------------------------------------------------------------------
resource cerImmutabilityPolicy 'Microsoft.Storage/storageAccounts/blobServices/containers/immutabilityPolicies@2023-05-01' = {
  parent: cerArtifactsContainer
  name: 'default'
  properties: {
    immutabilityPeriodSinceCreationInDays: 2555  // 7 years (365 * 7)
    allowProtectedAppendWrites: false              // WORM: no appends to existing blobs
  }
}

// ---------------------------------------------------------------------------
// Role Assignment: Function App MI → Storage Blob Data Contributor
// Scoped to the storage account (which contains only cer-artifacts).
// ---------------------------------------------------------------------------
var storageBlobDataContributorRole = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource storageBlobRbac 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, functionAppPrincipalId, storageBlobDataContributorRole)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId(
      'Microsoft.Authorization/roleDefinitions',
      storageBlobDataContributorRole
    )
    principalId: functionAppPrincipalId
    principalType: 'ServicePrincipal'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Blob account URL (https://<name>.blob.core.windows.net)')
output blobAccountUrl string = storageAccount.properties.primaryEndpoints.blob

@description('Storage account resource ID')
output storageAccountId string = storageAccount.id

@description('cer-artifacts container name')
output cerArtifactsContainerName string = cerArtifactsContainer.name
