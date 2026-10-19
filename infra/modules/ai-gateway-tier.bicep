// API Management AI Gateway tier (preview): a separate resource type, deployed side by side with the
// Basic v2 instance for comparison. Only the empty gateway is created here; models, MCP servers,
// policies and runtime keys are configured in the AI Gateway portal (https://ai.gateway.azure.com).
// No backend subnet, so traffic stays public, like Basic v2.
@allowed(['swedencentral', 'eastus2'])
@description('AI Gateway tier preview regions.')
param location string
param suffix string
param tags object
// ARM accepts only these two (the spec's 'AIGateway' example is rejected); the docs don't describe them yet.
@allowed(['Consumption', 'Premium'])
param skuName string = 'Consumption'

// Bicep has no types for this resource yet (BCP081), so properties aren't validated at build time.
#disable-next-line BCP081
resource aiGateway 'Microsoft.ApiManagement/aigateways@2026-09-01-preview' = {
  name: 'aigw-${suffix}'
  location: location
  tags: tags
  sku: { name: skuName }
  identity: { type: 'SystemAssigned' }
}

output name string = aiGateway.name
output hostname string = aiGateway.properties.frontend.defaultHostname
