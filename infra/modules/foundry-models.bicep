// Foundry account that only hosts the model. No projects: it is a pure inference backend behind APIM.
// Key auth is disabled; only the APIM managed identity gets data-plane access.
param location string
param suffix string
param tags object
param modelName string
param modelVersion string
param modelCapacity int
param apimPrincipalId string

var cognitiveServicesOpenAIUser = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2026-07-01' = {
  name: 'aif-models-${suffix}'
  location: location
  tags: tags
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: 'aif-models-${suffix}'
    disableLocalAuth: true
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: false
  }
}

resource deployment 'Microsoft.CognitiveServices/accounts/deployments@2026-07-01' = {
  parent: account
  name: modelName
  sku: {
    name: 'GlobalStandard'
    capacity: modelCapacity
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: modelName
      version: modelVersion
    }
    versionUpgradeOption: 'OnceNewDefaultVersionAvailable'
  }
}

resource apimCanCallModels 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: account
  name: guid(account.id, apimPrincipalId, cognitiveServicesOpenAIUser)
  properties: {
    principalId: apimPrincipalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUser)
  }
}

output endpoint string = 'https://${account.properties.customSubDomainName}.cognitiveservices.azure.com'
output name string = account.name
