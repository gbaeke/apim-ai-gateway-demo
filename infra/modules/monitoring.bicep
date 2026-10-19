// Two isolated observability stacks:
//  - "apim":   gateway logs, LLM prompt/completion logs, MCP logs, token metrics
//  - "agents": agent traces (OpenTelemetry GenAI), Container Apps logs; connected to the Foundry projects
param location string
param suffix string
param tags object

resource lawApim 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: 'log-apim-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appiApim 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-apim-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: lawApim.id
  }
}

resource lawAgents 'Microsoft.OperationalInsights/workspaces@2025-02-01' = {
  name: 'log-agents-${suffix}'
  location: location
  tags: tags
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appiAgents 'Microsoft.Insights/components@2020-02-02' = {
  name: 'appi-agents-${suffix}'
  location: location
  tags: tags
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: lawAgents.id
  }
}

output lawApimId string = lawApim.id
output appiApimName string = appiApim.name
output appiApimId string = appiApim.id
output lawAgentsName string = lawAgents.name
output lawAgentsId string = lawAgents.id
output appiAgentsName string = appiAgents.name
output appiAgentsId string = appiAgents.id
