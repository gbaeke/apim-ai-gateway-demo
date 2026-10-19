// APIM AI Gateway demo: two LangChain Deep Agents on Container Apps whose model, MCP and A2A traffic
// all flows through API Management. Deploy with scripts/deploy.sh.
targetScope = 'resourceGroup'

param location string = resourceGroup().location
param publisherEmail string
@description('Object id of the person deploying; gets rights to register the external agents in Foundry.')
param deployerPrincipalId string = ''
param modelName string = 'gpt-6-luna'
param modelVersion string = '2026-09-22'
@description('Model capacity in thousands of tokens per minute.')
param modelCapacity int = 300
@description('False on the first run: images do not exist yet, so the container apps are skipped.')
param deployApps bool = true
param imageTag string = 'latest'
@secure()
@description('Shared secret APIM sends to the specialist; scripts/deploy.sh keeps it stable across runs.')
param specialistGatewayKey string
@description('Also deploy an empty AI Gateway tier (preview) instance next to Basic v2, for comparison.')
param deployAiGatewayTier bool = false
@description('AI Gateway tier preview regions only: swedencentral or eastus2.')
param aiGatewayLocation string = 'swedencentral'
@description('AI Gateway tier SKU: Consumption or Premium.')
param aiGatewaySku string = 'Consumption'

var suffix = take(uniqueString(resourceGroup().id), 6)
var tags = { demo: 'apim-ai-gateway' }
var agentNames = ['orchestrator-agent', 'specialist-agent']
var modelEndpoint = 'https://aif-models-${suffix}.cognitiveservices.azure.com'

// ---------- Identities + registry ----------

resource orchestratorIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-orchestrator-agent'
  location: location
  tags: tags
}

resource specialistIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2024-11-30' = {
  name: 'id-specialist-agent'
  location: location
  tags: tags
}

resource acr 'Microsoft.ContainerRegistry/registries@2025-04-01' = {
  name: 'acr${suffix}${uniqueString(resourceGroup().id, 'acr')}'
  location: location
  tags: tags
  sku: { name: 'Basic' }
  properties: { adminUserEnabled: false }
}

var acrPull = '7f951dda-4ed3-4680-a7ca-43fe172d538d'
resource orchestratorAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, orchestratorIdentity.id, acrPull)
  properties: {
    principalId: orchestratorIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
  }
}

resource specialistAcrPull 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: acr
  name: guid(acr.id, specialistIdentity.id, acrPull)
  properties: {
    principalId: specialistIdentity.properties.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', acrPull)
  }
}

// ---------- Observability + Foundry ----------

module monitoring 'modules/monitoring.bicep' = {
  name: 'monitoring'
  params: { location: location, suffix: suffix, tags: tags }
}

module foundryAgents 'modules/foundry-agents.bicep' = {
  name: 'foundry-agents'
  params: {
    location: location
    suffix: suffix
    tags: tags
    appInsightsName: monitoring.outputs.appiAgentsName
    agentNames: agentNames
    deployerPrincipalId: deployerPrincipalId
  }
}

module containerEnv 'modules/container-env.bicep' = {
  name: 'container-env'
  params: {
    location: location
    suffix: suffix
    tags: tags
    lawName: monitoring.outputs.lawAgentsName
  }
}

// ---------- Gateway + model ----------

module apim 'modules/apim.bicep' = {
  name: 'apim'
  params: {
    location: location
    suffix: suffix
    tags: tags
    publisherEmail: publisherEmail
    modelEndpoint: modelEndpoint
    specialistUrl: 'https://ca-specialist.${containerEnv.outputs.defaultDomain}'
    orchestratorClientId: orchestratorIdentity.properties.clientId
    specialistClientId: specialistIdentity.properties.clientId
    specialistGatewayKey: specialistGatewayKey
    lawId: monitoring.outputs.lawApimId
    appInsightsName: monitoring.outputs.appiApimName
  }
}

module aiGatewayTier 'modules/ai-gateway-tier.bicep' = if (deployAiGatewayTier) {
  name: 'ai-gateway-tier'
  params: { location: aiGatewayLocation, suffix: suffix, tags: tags, skuName: aiGatewaySku }
}

module foundryModels 'modules/foundry-models.bicep' = {
  name: 'foundry-models'
  params: {
    location: location
    suffix: suffix
    tags: tags
    modelName: modelName
    modelVersion: modelVersion
    modelCapacity: modelCapacity
    apimPrincipalId: apim.outputs.principalId
  }
}

// ---------- Agents ----------

resource appiAgents 'Microsoft.Insights/components@2020-02-02' existing = {
  name: 'appi-agents-${suffix}'
  dependsOn: [monitoring]
}

module apps 'modules/container-apps.bicep' = if (deployApps) {
  name: 'container-apps'
  dependsOn: [orchestratorAcrPull, specialistAcrPull]
  params: {
    location: location
    tags: tags
    envName: containerEnv.outputs.name
    acrLoginServer: acr.properties.loginServer
    apimGatewayUrl: apim.outputs.gatewayUrl
    modelName: modelName
    agentsAppInsightsConnectionString: appiAgents.properties.ConnectionString
    orchestratorIdentityId: orchestratorIdentity.id
    orchestratorClientId: orchestratorIdentity.properties.clientId
    specialistIdentityId: specialistIdentity.id
    specialistClientId: specialistIdentity.properties.clientId
    specialistGatewayKey: specialistGatewayKey
    orchestratorImage: '${acr.properties.loginServer}/orchestrator-agent:${imageTag}'
    specialistImage: '${acr.properties.loginServer}/specialist-agent:${imageTag}'
  }
}

output acrName string = acr.name
output apimName string = apim.outputs.name
output apimGatewayUrl string = apim.outputs.gatewayUrl
output aiGatewayHostname string = deployAiGatewayTier ? aiGatewayTier!.outputs.hostname : ''
output modelEndpoint string = foundryModels.outputs.endpoint
output foundryAgentsAccount string = foundryAgents.outputs.name
output foundryProjectEndpoints array = foundryAgents.outputs.projectEndpoints
output orchestratorUrl string = deployApps ? apps!.outputs.orchestratorUrl : ''
output specialistUrl string = deployApps ? apps!.outputs.specialistUrl : ''
output orchestratorClientId string = orchestratorIdentity.properties.clientId
output specialistClientId string = specialistIdentity.properties.clientId
