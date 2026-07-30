// infra/modules/network.bicep
// Hub-spoke VNet, private endpoints, NSG, optional Azure Firewall.
// All data-plane traffic stays inside the VNet; no public internet access to
// Key Vault, Cosmos, Storage, or Service Bus.

@description('Name prefix for all resources')
param namePrefix string

@description('Location for all resources')
param location string

@description('Resource tags')
param tags object

@description('Enable private endpoints (false saves cost in dev)')
param enablePrivateEndpoints bool = true

@description('Enable Azure Firewall (true only in prod)')
param enableFirewall bool = false

// ---------------------------------------------------------------------------
// VNet
// ---------------------------------------------------------------------------

resource vnet 'Microsoft.Network/virtualNetworks@2023-09-01' = {
  name: '${namePrefix}-vnet'
  location: location
  tags: tags
  properties: {
    addressSpace: {
      addressPrefixes: ['10.0.0.0/16']
    }
    subnets: [
      {
        name: 'functions'
        properties: {
          addressPrefix: '10.0.1.0/24'
          delegations: [
            {
              name: 'funcDelegation'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'private-endpoints'
        properties: {
          addressPrefix: '10.0.2.0/24'
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'apim'
        properties: {
          addressPrefix: '10.0.3.0/24'
        }
      }
      {
        name: 'firewall'
        properties: {
          addressPrefix: '10.0.4.0/24'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// NSG — functions subnet
// Allow outbound to Azure services; deny all inbound from internet.
// ---------------------------------------------------------------------------

resource nsgFunctions 'Microsoft.Network/networkSecurityGroups@2023-09-01' = {
  name: '${namePrefix}-nsg-functions'
  location: location
  tags: tags
  properties: {
    securityRules: [
      {
        name: 'DenyInternetInbound'
        properties: {
          priority: 100
          direction: 'Inbound'
          access: 'Deny'
          protocol: '*'
          sourceAddressPrefix: 'Internet'
          sourcePortRange: '*'
          destinationAddressPrefix: '*'
          destinationPortRange: '*'
        }
      }
      {
        name: 'AllowAzureMonitorOutbound'
        properties: {
          priority: 200
          direction: 'Outbound'
          access: 'Allow'
          protocol: 'Tcp'
          sourceAddressPrefix: 'VirtualNetwork'
          sourcePortRange: '*'
          destinationAddressPrefix: 'AzureMonitor'
          destinationPortRange: '443'
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Private DNS Zones (required for private endpoints)
// ---------------------------------------------------------------------------

var privateDnsZones = enablePrivateEndpoints ? [
  'privatelink.documents.azure.com'
  'privatelink.vault.azure.net'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.servicebus.windows.net'
] : []

resource dnsZones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for zone in privateDnsZones: {
  name: zone
  location: 'global'
  tags: tags
}]

resource dnsZoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (zone, i) in privateDnsZones: {
  parent: dnsZones[i]
  name: '${namePrefix}-${replace(zone, '.', '-')}-link'
  location: 'global'
  properties: {
    virtualNetwork: {
      id: vnet.id
    }
    registrationEnabled: false
  }
}]

// ---------------------------------------------------------------------------
// Azure Firewall (prod only)
// ---------------------------------------------------------------------------

resource firewallPip 'Microsoft.Network/publicIPAddresses@2023-09-01' = if (enableFirewall) {
  name: '${namePrefix}-fw-pip'
  location: location
  tags: tags
  sku: {
    name: 'Standard'
    tier: 'Regional'
  }
  properties: {
    publicIPAllocationMethod: 'Static'
  }
}

resource firewall 'Microsoft.Network/azureFirewalls@2023-09-01' = if (enableFirewall) {
  name: '${namePrefix}-fw'
  location: location
  tags: tags
  properties: {
    sku: {
      name: 'AZFW_VNet'
      tier: 'Standard'
    }
    ipConfigurations: [
      {
        name: 'ipconfig'
        properties: {
          subnet: {
            id: '${vnet.id}/subnets/firewall'
          }
          publicIPAddress: {
            id: enableFirewall ? firewallPip.id : ''
          }
        }
      }
    ]
    applicationRuleCollections: [
      {
        name: 'allow-azure-services'
        properties: {
          priority: 100
          action: { type: 'Allow' }
          rules: [
            {
              name: 'allow-aad'
              protocols: [{ protocolType: 'Https', port: 443 }]
              targetFqdns: ['login.microsoftonline.com', '*.microsoft.com']
              sourceAddresses: ['10.0.0.0/16']
            }
          ]
        }
      }
    ]
  }
}

// ---------------------------------------------------------------------------
// Outputs
// ---------------------------------------------------------------------------

output vnetId string = vnet.id
output functionsSubnetId string = '${vnet.id}/subnets/functions'
output privateEndpointsSubnetId string = '${vnet.id}/subnets/private-endpoints'
output apimSubnetId string = '${vnet.id}/subnets/apim'
