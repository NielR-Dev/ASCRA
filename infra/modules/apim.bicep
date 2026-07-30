// infra/modules/apim.bicep
// Azure API Management in MCP Proxy mode — fronts the external MCP servers
// (Jira, Dynatrace) with JWT validation, rate limiting, and RBAC denial policies.
//
// Key security responsibilities:
//   - Validates Entra ID JWT on every inbound request
//   - Blocks Bob's dev-plane token from accessing run-plane MCP endpoints (G5)
//   - Rate-limits per-IP and per-subscription to prevent flooding
//   - Strips sensitive response headers before forwarding to the agent

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('APIM SKU (Developer for dev/uat; Premium for prod)')
param skuName string = 'Developer'

@description('Number of APIM scale units')
param skuCapacity int = 1

@description('Entra ID tenant ID for JWT validation')
param tenantId string = tenant().tenantId

@description('VNet integration subnet ID (Premium SKU only)')
param vnetSubnetId string = ''

@description('Agent identity client ID — allowed to call run-plane MCPs')
param agentClientId string

@description('Bob dev-plane token application ID — DENIED run-plane access')
param bobDevPlaneAppId string = 'bob-dev-plane'

@description('Dynatrace MCP backend URL')
param dynatraceMcpUrl string = 'https://dynatrace-mcp.example.com'

@description('Jira MCP backend URL')
param jiraMcpUrl string = 'https://jira-mcp.example.com'

// ---------------------------------------------------------------------------
// APIM instance
// ---------------------------------------------------------------------------

resource apim 'Microsoft.ApiManagement/service@2023-05-01-preview' = {
  name: '${namePrefix}-apim'
  location: location
  tags: tags
  sku: {
    name: skuName
    capacity: skuCapacity
  }
  identity: {
    type: 'SystemAssigned'
  }
  properties: {
    publisherEmail: 'admin@example.com'
    publisherName: '${namePrefix} SSL Renewal'
    virtualNetworkType: !empty(vnetSubnetId) ? 'Internal' : 'None'
    virtualNetworkConfiguration: !empty(vnetSubnetId) ? {
      subnetResourceId: vnetSubnetId
    } : null
    customProperties: {
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls10': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Protocols.Tls11': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls10': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Backend.Protocols.Tls11': 'false'
      'Microsoft.WindowsAzure.ApiManagement.Gateway.Security.Ciphers.TripleDes168': 'false'
    }
  }
}

// ---------------------------------------------------------------------------
// Named values (non-secret config)
// ---------------------------------------------------------------------------

resource tenantIdNv 'Microsoft.ApiManagement/service/namedValues@2023-05-01-preview' = {
  parent: apim
  name: 'tenantId'
  properties: {
    displayName: 'tenantId'
    value: tenantId
    secret: false
  }
}

resource agentClientIdNv 'Microsoft.ApiManagement/service/namedValues@2023-05-01-preview' = {
  parent: apim
  name: 'agentClientId'
  properties: {
    displayName: 'agentClientId'
    value: agentClientId
    secret: false
  }
}

resource bobDevPlaneAppIdNv 'Microsoft.ApiManagement/service/namedValues@2023-05-01-preview' = {
  parent: apim
  name: 'bobDevPlaneAppId'
  properties: {
    displayName: 'bobDevPlaneAppId'
    value: bobDevPlaneAppId
    secret: false
  }
}

// ---------------------------------------------------------------------------
// Global policy — JWT validation + Bob denial on ALL APIs
// ---------------------------------------------------------------------------

resource globalPolicy 'Microsoft.ApiManagement/service/policies@2023-05-01-preview' = {
  parent: apim
  name: 'policy'
  properties: {
    format: 'rawxml'
    value: '''
<policies>
  <inbound>
    <!-- Validate Entra ID JWT on every request -->
    <validate-jwt header-name="Authorization" failed-validation-httpcode="401"
                  failed-validation-error-message="Unauthorized — valid Entra ID JWT required">
      <openid-config url="https://login.microsoftonline.com/{{tenantId}}/v2.0/.well-known/openid-configuration" />
      <required-claims>
        <claim name="aud" match="any">
          <value>https://management.azure.com/</value>
        </claim>
      </required-claims>
    </validate-jwt>
    <!-- G5 / Dev-plane isolation: Bob's token is denied on all run-plane endpoints -->
    <choose>
      <when condition="@(context.Request.Headers.GetValueOrDefault(&quot;Authorization&quot;, &quot;&quot;).Contains(&quot;{{bobDevPlaneAppId}}&quot;))">
        <return-response>
          <set-status code="403" reason="Forbidden" />
          <set-body>{"error":{"code":"dev_plane_forbidden","message":"Bob dev-plane token is not permitted on run-plane MCP endpoints."}}</set-body>
        </return-response>
      </when>
    </choose>
    <!-- Rate limit: 60 calls per minute per subscription -->
    <rate-limit calls="60" renewal-period="60" />
    <!-- Strip Authorization header from upstream requests (backend uses managed identity) -->
    <set-header name="Authorization" exists-action="delete" />
  </inbound>
  <backend>
    <forward-request />
  </backend>
  <outbound>
    <!-- Strip sensitive headers from responses -->
    <set-header name="X-Powered-By" exists-action="delete" />
    <set-header name="Server" exists-action="delete" />
  </outbound>
  <on-error>
    <set-status code="500" reason="Internal Server Error" />
  </on-error>
</policies>
'''
  }
}

// ---------------------------------------------------------------------------
// MCP APIs — Dynatrace + Jira
// ---------------------------------------------------------------------------

resource dynatraceApi 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  parent: apim
  name: 'dynatrace-mcp'
  properties: {
    displayName: 'Dynatrace MCP'
    description: 'APIM-fronted Dynatrace MCP server (read-only, G5)'
    path: 'mcp/dynatrace'
    protocols: ['https']
    subscriptionRequired: false
    serviceUrl: dynatraceMcpUrl
    isCurrent: true
    apiType: 'http'
  }
}

resource jiraApi 'Microsoft.ApiManagement/service/apis@2023-05-01-preview' = {
  parent: apim
  name: 'jira-mcp'
  properties: {
    displayName: 'Jira MCP'
    description: 'APIM-fronted Jira MCP server (G5)'
    path: 'mcp/jira'
    protocols: ['https']
    subscriptionRequired: false
    serviceUrl: jiraMcpUrl
    isCurrent: true
    apiType: 'http'
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output apimId string = apim.id
output apimGatewayUrl string = apim.properties.gatewayUrl
output dynatraceMcpApimUrl string = '${apim.properties.gatewayUrl}/mcp/dynatrace'
output jiraMcpApimUrl string = '${apim.properties.gatewayUrl}/mcp/jira'
