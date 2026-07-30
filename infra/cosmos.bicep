// infra/cosmos.bicep
// Azure Cosmos DB for NoSQL — ssl_renewal database with 4 containers.
//
// Containers:
//   workflow_state  — PK: /workflow_id, no TTL, PITR enabled
//   audit_log       — PK: /workflow_id, no TTL, append-only
//   idempotency     — PK: /idempotency_key, TTL: 30 days
//   batch           — PK: /batch_id, no TTL
//
// Security (G7/G8):
//   - No private key material, CSR bytes, or CER bytes are stored here (enforced by cosmos_repo.py).
//   - Data-plane RBAC: Function App Managed Identity is granted Cosmos Built-in Data Contributor
//     on the ssl_renewal database only (not account-level).

@description('Azure region for deployment')
param location string = resourceGroup().location

@description('Name of the Cosmos DB account')
param accountName string

@description('Environment tag for resource grouping')
param environment string = 'prod'

@description('Managed Identity principal ID of the Function App (for RBAC assignment)')
param functionAppPrincipalId string

// ---------------------------------------------------------------------------
// Cosmos DB Account
// ---------------------------------------------------------------------------
resource cosmosAccount 'Microsoft.DocumentDB/databaseAccounts@2024-02-15-preview' = {
  name: accountName
  location: location
  kind: 'GlobalDocumentDB'
  tags: {
    environment: environment
    system: 'ssl-renewal-agent'
  }
  properties: {
    databaseAccountOfferType: 'Standard'
    consistencyPolicy: {
      defaultConsistencyLevel: 'Session'
    }
    locations: [
      {
        locationName: location
        failoverPriority: 0
        isZoneRedundant: true  // Zone-redundant for HA within the region
      }
    ]
    backupPolicy: {
      type: 'Continuous'
      continuousModeProperties: {
        tier: 'Continuous30Days'  // PITR: 30-day restore window (7-day minimum; 30 for safety)
      }
    }
    // Disable public network access; rely on Private Endpoint (network.bicep wires this)
    publicNetworkAccess: 'Disabled'
    isVirtualNetworkFilterEnabled: false  // Private Endpoint handles network isolation
    enableAutomaticFailover: false         // Single region for v1; set true if adding a read region
    enableMultipleWriteLocations: false
    // Disable local auth — all access via Managed Identity (G8)
    disableLocalAuth: true
    networkAclBypass: 'None'
  }
}

// ---------------------------------------------------------------------------
// Database
// ---------------------------------------------------------------------------
resource sslRenewalDb 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases@2024-02-15-preview' = {
  parent: cosmosAccount
  name: 'ssl_renewal'
  properties: {
    resource: {
      id: 'ssl_renewal'
    }
    options: {
      // No throughput at the database level; each container has its own autoscale
    }
  }
}

// ---------------------------------------------------------------------------
// Container: workflow_state
// ---------------------------------------------------------------------------
resource workflowStateContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-02-15-preview' = {
  parent: sslRenewalDb
  name: 'workflow_state'
  properties: {
    resource: {
      id: 'workflow_state'
      partitionKey: {
        paths: ['/workflow_id']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: -1  // No TTL — retain indefinitely
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/state/?' }
          { path: '/cn/?' }
          { path: '/owning_application/?' }
          { path: '/updated_at/?' }
          { path: '/batch_id/?' }
          { path: '/created_at/?' }
        ]
        excludedPaths: [
          // Exclude large, non-queryable string fields from index to reduce RU cost
          { path: '/"_etag"/?' }
          { path: '/csr/csr_pem_sha256/?' }  // Not queried; kept as checksum only
        ]
        compositeIndexes: [
          [
            { path: '/state', order: 'ascending' }
            { path: '/updated_at', order: 'descending' }
          ]
        ]
      }
    }
    options: {
      autoscaleSettings: {
        maxThroughput: 4000  // 400–4000 RU/s autoscale; expand if 100-cert wave hits limits
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container: audit_log
// ---------------------------------------------------------------------------
resource auditLogContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-02-15-preview' = {
  parent: sslRenewalDb
  name: 'audit_log'
  properties: {
    resource: {
      id: 'audit_log'
      partitionKey: {
        paths: ['/workflow_id']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: -1  // No TTL — retain for compliance (Purview policy may add lifecycle)
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/seq/?' }
          { path: '/timestamp/?' }
          { path: '/actor/?' }
          { path: '/action/?' }
          { path: '/state_after/?' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
        compositeIndexes: [
          [
            { path: '/workflow_id', order: 'ascending' }
            { path: '/seq', order: 'ascending' }
          ]
        ]
      }
    }
    options: {
      autoscaleSettings: {
        maxThroughput: 4000  // Audit writes are bursty during active wave processing
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container: idempotency
// ---------------------------------------------------------------------------
resource idempotencyContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-02-15-preview' = {
  parent: sslRenewalDb
  name: 'idempotency'
  properties: {
    resource: {
      id: 'idempotency'
      partitionKey: {
        paths: ['/idempotency_key']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: 2592000  // 30 days in seconds (idempotency keys expire after 30 days)
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/idempotency_key/?' }
          { path: '/recorded_at/?' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
        ]
      }
    }
    options: {
      autoscaleSettings: {
        maxThroughput: 1000  // Low throughput: idempotency writes are infrequent
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Container: batch
// ---------------------------------------------------------------------------
resource batchContainer 'Microsoft.DocumentDB/databaseAccounts/sqlDatabases/containers@2024-02-15-preview' = {
  parent: sslRenewalDb
  name: 'batch'
  properties: {
    resource: {
      id: 'batch'
      partitionKey: {
        paths: ['/batch_id']
        kind: 'Hash'
        version: 2
      }
      defaultTtl: -1  // No TTL — retain for batch audit/dashboard
      indexingPolicy: {
        indexingMode: 'consistent'
        automatic: true
        includedPaths: [
          { path: '/source/?' }
          { path: '/created_at/?' }
          { path: '/aggregate/by_state/?' }
        ]
        excludedPaths: [
          { path: '/"_etag"/?' }
          { path: '/children/[]/*' }  // Exclude children array from index (large)
        ]
      }
    }
    options: {
      autoscaleSettings: {
        maxThroughput: 1000  // Low throughput: batch writes are infrequent
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Data-plane RBAC: Function App MI → Cosmos DB Built-in Data Contributor
// Scoped to the ssl_renewal database only (not account-level).
// ---------------------------------------------------------------------------
var cosmosDataContributorRoleId = '00000000-0000-0000-0000-000000000002'

resource cosmosRbacAssignment 'Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments@2024-02-15-preview' = {
  parent: cosmosAccount
  name: guid(cosmosAccount.id, functionAppPrincipalId, cosmosDataContributorRoleId)
  properties: {
    roleDefinitionId: '${cosmosAccount.id}/sqlRoleDefinitions/${cosmosDataContributorRoleId}'
    principalId: functionAppPrincipalId
    scope: '${cosmosAccount.id}/dbs/${sslRenewalDb.name}'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------
@description('Cosmos DB account endpoint')
output cosmosEndpoint string = cosmosAccount.properties.documentEndpoint

@description('Cosmos DB account resource ID')
output cosmosAccountId string = cosmosAccount.id
