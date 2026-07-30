// infra/modules/servicebus.bicep
// Azure Service Bus namespace + queue for renewal event delivery.
// Premium SKU with zone-redundancy in prod; Standard in dev/uat.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('Service Bus SKU (Standard for dev; Premium for uat/prod)')
param skuName string = 'Standard'

@description('Enable zone redundancy (Premium SKU only)')
param zoneRedundant bool = false

@description('Enable private endpoint')
param enablePrivateEndpoint bool = false

@description('Private endpoint subnet ID')
param privateEndpointSubnetId string = ''

// ---------------------------------------------------------------------------
// Namespace
// ---------------------------------------------------------------------------

resource sbNamespace 'Microsoft.ServiceBus/namespaces@2022-10-01-preview' = {
  name: '${namePrefix}-sb'
  location: location
  tags: tags
  sku: {
    name: skuName
    tier: skuName
  }
  properties: {
    zoneRedundant: zoneRedundant
    minimumTlsVersion: '1.2'
    publicNetworkAccess: enablePrivateEndpoint ? 'Disabled' : 'Enabled'
  }
}

// ---------------------------------------------------------------------------
// Queues
// ---------------------------------------------------------------------------

// Main renewal queue — Dynatrace events → orchestrator
resource renewalQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: sbNamespace
  name: 'ssl-renewals'
  properties: {
    maxSizeInMegabytes: 1024
    requiresDuplicateDetection: true           // idempotency: duplicate events dropped
    duplicateDetectionHistoryTimeWindow: 'PT1H'
    maxDeliveryCount: 5
    deadLetteringOnMessageExpiration: true     // dead-letter if not processed in time
    messageTimeToLive: 'P7D'                   // 7 days max TTL
    lockDuration: 'PT5M'
    enablePartitioning: skuName == 'Premium'   // partitioning only available in Premium
  }
}

// Dead-letter review queue — manual review of failed renewals
resource dlqQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: sbNamespace
  name: 'ssl-renewals-dlq-review'
  properties: {
    maxSizeInMegabytes: 512
    messageTimeToLive: 'P30D'
    lockDuration: 'PT5M'
  }
}

// Approval callback queue — Teams approval responses
resource approvalCallbackQueue 'Microsoft.ServiceBus/namespaces/queues@2022-10-01-preview' = {
  parent: sbNamespace
  name: 'approval-callbacks'
  properties: {
    maxSizeInMegabytes: 512
    requiresDuplicateDetection: true
    duplicateDetectionHistoryTimeWindow: 'PT1H'
    maxDeliveryCount: 3
    messageTimeToLive: 'P3D'
    lockDuration: 'PT1M'
  }
}

// ---------------------------------------------------------------------------
// Private endpoint
// ---------------------------------------------------------------------------

resource sbPrivateEndpoint 'Microsoft.Network/privateEndpoints@2023-09-01' = if (enablePrivateEndpoint) {
  name: '${namePrefix}-sb-pe'
  location: location
  tags: tags
  properties: {
    subnet: {
      id: privateEndpointSubnetId
    }
    privateLinkServiceConnections: [
      {
        name: '${namePrefix}-sb-pe-conn'
        properties: {
          privateLinkServiceId: sbNamespace.id
          groupIds: ['namespace']
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output serviceBusNamespaceId string = sbNamespace.id
output serviceBusNamespaceName string = sbNamespace.name
output renewalQueueName string = renewalQueue.name
output approvalCallbackQueueName string = approvalCallbackQueue.name
