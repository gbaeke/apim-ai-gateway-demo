// Foundry account used purely for agent observability: one project per agent, each connected to the
// agents' Application Insights so traces show up under the project (and under the registered external agent).
param location string
param suffix string
param tags object
param appInsightsName string
param agentNames array
param deployerPrincipalId string

var azureAIUser = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
var logAnalyticsReader = '73c42c96-874c-492b-b04d-ab87d138a893'

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource account 'Microsoft.CognitiveServices/accounts@2026-07-01' = {
  name: 'aif-agents-${suffix}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'aif-agents-${suffix}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
  }
}

@batchSize(1)
resource projects 'Microsoft.CognitiveServices/accounts/projects@2026-07-01' = [
  for agent in agentNames: {
    parent: account
    name: agent
    location: location
    tags: tags
    identity: { type: 'SystemAssigned' }
    properties: {
      displayName: agent
      description: 'Observability project for ${agent} (runs on Azure Container Apps)'
    }
  }
]

@batchSize(1)
resource appInsightsConnections 'Microsoft.CognitiveServices/accounts/projects/connections@2026-07-01' = [
  for (agent, i) in agentNames: {
    parent: projects[i]
    // Connection names are unique across the whole account, not per project.
    name: 'appi-${agent}'
    properties: {
      category: 'AppInsights'
      target: appInsights.id
      authType: 'ApiKey'
      isSharedToAll: true
      credentials: { key: appInsights.properties.ConnectionString }
      metadata: {
        ApiType: 'Azure'
        ResourceId: appInsights.id
      }
    }
  }
]

// Projects read traces from App Insights.
resource projectsReadTraces 'Microsoft.Authorization/roleAssignments@2022-04-01' = [
  for (agent, i) in agentNames: {
    scope: appInsights
    name: guid(appInsights.id, agent, logAnalyticsReader)
    properties: {
      principalId: projects[i].identity.principalId
      principalType: 'ServicePrincipal'
      roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', logAnalyticsReader)
    }
  }
]

// The deployer registers the external agents through the Foundry data plane.
resource deployerIsAIUser 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  scope: account
  name: guid(account.id, deployerPrincipalId, azureAIUser)
  properties: {
    principalId: deployerPrincipalId
    principalType: 'User'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIUser)
  }
}

output name string = account.name
output projectEndpoints array = [for agent in agentNames: 'https://${account.properties.customSubDomainName}.services.ai.azure.com/api/projects/${agent}']
