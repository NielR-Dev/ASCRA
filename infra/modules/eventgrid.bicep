// infra/modules/eventgrid.bicep
// Event Grid subscription: Dynatrace webhook → Service Bus renewal queue.
// The Event Grid System Topic listens on the Service Bus namespace and
// forwards filtered SSL expiry events to the ssl-renewals queue.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('Service Bus namespace ID (event destination)')
param serviceBusNamespaceId string

@description('Service Bus renewal queue resource ID')
param renewalQueueId string

@description('Dynatrace webhook endpoint URL (source)')
param dynatraceWebhookUrl string = ''

// ---------------------------------------------------------------------------
// Event Grid System Topic — Service Bus source
// We use a custom topic to accept Dynatrace HTTP POST payloads via the
// webhook ingest endpoint, then route to Service Bus.
// ---------------------------------------------------------------------------

resource customTopic 'Microsoft.EventGrid/topics@2023-12-15-preview' = {
  name: '${namePrefix}-eg-topic'
  location: location
  tags: tags
  properties: {
    inputSchema: 'CloudEventSchemaV1_0'
    publicNetworkAccess: 'Enabled'    // Dynatrace posts from external; APIM fronts this in prod
    disableLocalAuth: true            // Entra ID auth only; no SAS keys
    inboundIpRules: []
  }
  identity: {
    type: 'SystemAssigned'
  }
}

// ---------------------------------------------------------------------------
// Event Subscription: Custom Topic → Service Bus Queue
// Filtered to only forward events with type = 'ssl.certificate.expiry'
// ---------------------------------------------------------------------------

resource eventSubscription 'Microsoft.EventGrid/topics/eventSubscriptions@2023-12-15-preview' = {
  parent: customTopic
  name: '${namePrefix}-ssl-expiry-sub'
  properties: {
    destination: {
      endpointType: 'ServiceBusQueue'
      properties: {
        resourceId: renewalQueueId
      }
    }
    filter: {
      includedEventTypes: [
        'ssl.certificate.expiry'
        'ssl.certificate.expiry.warning'
      ]
      advancedFilters: [
        {
          key: 'data.daysUntilExpiry'
          operatorType: 'NumberLessThanOrEquals'
          value: 30
        }
      ]
      isSubjectCaseSensitive: false
    }
    eventDeliverySchema: 'CloudEventSchemaV1_0'
    retryPolicy: {
      maxDeliveryAttempts: 30
      eventTimeToLiveInMinutes: 1440   // 24 hours
    }
    deadLetterDestination: {
      endpointType: 'StorageBlob'
      properties: {
        resourceId: ''   // populated at runtime via main.bicep param
        blobContainerName: 'eg-deadletter'
      }
    }
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output eventGridTopicId string = customTopic.id
output eventGridTopicEndpoint string = customTopic.properties.endpoint
output eventGridTopicPrincipalId string = customTopic.identity.principalId
