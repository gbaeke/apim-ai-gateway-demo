// API Management (Basic v2) acting as the AI gateway for three kinds of traffic:
//   /foundry/openai/v1  -> Foundry model (LLM API)
//   /mslearn/mcp        -> Microsoft Learn MCP server (MCP passthrough)
//   /specialist         -> specialist agent (A2A agent API, preview)
param location string
param suffix string
param tags object
param publisherEmail string
param modelEndpoint string
param specialistUrl string
param orchestratorClientId string
param specialistClientId string
@secure()
param specialistGatewayKey string
param lawId string
param appInsightsName string

var monitoringMetricsPublisher = '3913510d-42f4-4e42-8a64-420c390055eb'

resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
}

resource apim 'Microsoft.ApiManagement/service@2024-06-01-preview' = {
  name: 'apim-${suffix}'
  location: location
  tags: tags
  sku: {
    name: 'BasicV2'
    capacity: 1
  }
  identity: { type: 'SystemAssigned' }
  properties: {
    publisherEmail: publisherEmail
    publisherName: 'AI Gateway Demo'
  }
}

// ---------- Named values (editable live in the portal during the demo) ----------

var namedValues = {
  'tenant-id': tenant().tenantId
  'orchestrator-client-id': orchestratorClientId
  'specialist-client-id': specialistClientId
  'orchestrator-tpm': '50000'
  'specialist-tpm': '20000'
  // One MCP tool call is ~5 HTTP requests (initialize, initialized, tools/call, GET, DELETE).
  'mcp-calls-per-minute': '300'
  'a2a-calls-per-minute': '20'
}

resource nv 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = [
  for item in items(namedValues): {
    parent: apim
    name: item.key
    properties: {
      displayName: item.key
      value: item.value
    }
  }
]

resource nvGatewayKey 'Microsoft.ApiManagement/service/namedValues@2024-06-01-preview' = {
  parent: apim
  name: 'specialist-gateway-key'
  properties: {
    displayName: 'specialist-gateway-key'
    value: specialistGatewayKey
    secret: true
  }
}

// ---------- Observability ----------

resource apimPublishesMetrics 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  scope: appInsights
  name: guid(appInsights.id, apim.id, monitoringMetricsPublisher)
  properties: {
    principalId: apim.identity.principalId
    principalType: 'ServicePrincipal'
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', monitoringMetricsPublisher)
  }
}

resource appInsightsLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = {
  parent: apim
  name: 'appinsights'
  dependsOn: [apimPublishesMetrics]
  properties: {
    loggerType: 'applicationInsights'
    resourceId: appInsights.id
    credentials: {
      connectionString: appInsights.properties.ConnectionString
      identityClientId: 'SystemAssigned'
    }
    isBuffered: true
  }
}

resource azureMonitorLogger 'Microsoft.ApiManagement/service/loggers@2024-06-01-preview' = {
  parent: apim
  name: 'azuremonitor'
  properties: {
    loggerType: 'azureMonitor'
    isBuffered: false
  }
}

// Request bodies are not logged (payload logging breaks MCP streaming); LLM payloads go to
// ApiManagementGatewayLlmLog through the LLM API's own diagnostic below.
var noBodies = {
  request: { headers: [], body: { bytes: 0 } }
  response: { headers: ['x-apim-caller', 'x-ratelimit-remaining-tokens', 'x-tokens-consumed'], body: { bytes: 0 } }
}

resource appInsightsDiagnostic 'Microsoft.ApiManagement/service/diagnostics@2024-06-01-preview' = {
  parent: apim
  name: 'applicationinsights'
  properties: {
    loggerId: appInsightsLogger.id
    alwaysLog: 'allErrors'
    httpCorrelationProtocol: 'W3C'
    verbosity: 'information'
    logClientIp: true
    metrics: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: noBodies
    backend: noBodies
  }
}

resource azureMonitorDiagnostic 'Microsoft.ApiManagement/service/diagnostics@2024-06-01-preview' = {
  parent: apim
  name: 'azuremonitor'
  properties: {
    loggerId: azureMonitorLogger.id
    logClientIp: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    frontend: noBodies
    backend: noBodies
  }
}

resource diagnosticSettings 'Microsoft.Insights/diagnosticSettings@2021-05-01-preview' = {
  scope: apim
  name: 'to-log-analytics'
  properties: {
    workspaceId: lawId
    logAnalyticsDestinationType: 'Dedicated'
    logs: [
      { category: 'GatewayLogs', enabled: true }
      { category: 'GatewayLlmLogs', enabled: true }
      { category: 'GatewayMCPLogs', enabled: true }
    ]
    metrics: [{ category: 'AllMetrics', enabled: true }]
  }
}

// ---------- All-APIs policy: caller identity ----------

resource globalPolicy 'Microsoft.ApiManagement/service/policies@2024-06-01-preview' = {
  parent: apim
  name: 'policy'
  dependsOn: [nv]
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/global.xml')
  }
}

// ---------- Model API ----------

resource modelBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apim
  name: 'foundry-models'
  properties: {
    url: '${modelEndpoint}/openai/v1'
    protocol: 'http'
    description: 'Foundry account hosting gpt-6-luna'
    // Stop hammering the backend when it is throttling or failing.
    circuitBreaker: {
      rules: [
        {
          name: 'foundry-breaker'
          failureCondition: {
            count: 3
            interval: 'PT1M'
            statusCodeRanges: [{ min: 429, max: 429 }, { min: 500, max: 599 }]
          }
          tripDuration: 'PT1M'
          acceptRetryAfter: true
        }
      ]
    }
  }
}

resource llmApi 'Microsoft.ApiManagement/service/apis@2024-06-01-preview' = {
  parent: apim
  name: 'foundry-openai-v1'
  properties: {
    displayName: 'Foundry models (OpenAI v1)'
    description: 'OpenAI v1-compatible surface of the Foundry model account'
    path: 'foundry/openai/v1'
    protocols: ['https']
    subscriptionRequired: false
    serviceUrl: '${modelEndpoint}/openai/v1'
  }
}

var llmOperations = [
  { name: 'chat-completions', method: 'POST', url: '/chat/completions', display: 'Create chat completion' }
  { name: 'responses', method: 'POST', url: '/responses', display: 'Create response' }
  { name: 'models', method: 'GET', url: '/models', display: 'List models' }
]

resource llmOps 'Microsoft.ApiManagement/service/apis/operations@2024-06-01-preview' = [
  for op in llmOperations: {
    parent: llmApi
    name: op.name
    properties: {
      displayName: op.display
      method: op.method
      urlTemplate: op.url
    }
  }
]

resource llmPolicy 'Microsoft.ApiManagement/service/apis/policies@2024-06-01-preview' = {
  parent: llmApi
  name: 'policy'
  dependsOn: [nv, modelBackend]
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/llm-api.xml')
  }
}

// Prompts and completions -> ApiManagementGatewayLlmLog
resource llmAzureMonitorDiagnostic 'Microsoft.ApiManagement/service/apis/diagnostics@2024-06-01-preview' = {
  parent: llmApi
  name: 'azuremonitor'
  properties: {
    loggerId: azureMonitorLogger.id
    logClientIp: true
    sampling: { samplingType: 'fixed', percentage: 100 }
    largeLanguageModel: {
      logs: 'enabled'
      requests: { messages: 'all', maxSizeInBytes: 262144 }
      responses: { messages: 'all', maxSizeInBytes: 262144 }
    }
  }
}

// ---------- MCP API: Microsoft Learn ----------

// Passthrough = an MCP API bound to a backend. (serviceUrl + mcpProperties.endpoints creates an
// APIM-hosted MCP server with zero tools instead; the ai-gateway samples use backendId.)
resource mcpBackend 'Microsoft.ApiManagement/service/backends@2024-06-01-preview' = {
  parent: apim
  name: 'mslearn-mcp'
  properties: {
    url: 'https://learn.microsoft.com/api/mcp'
    protocol: 'http'
    description: 'Public Microsoft Learn MCP server'
  }
}

resource mcpApi 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'mslearn-mcp'
  properties: {
    type: 'mcp'
    displayName: 'Microsoft Learn MCP'
    description: 'Passthrough to the public Microsoft Learn MCP server'
    path: 'mslearn/mcp'
    protocols: ['https']
    subscriptionRequired: false
    #disable-next-line BCP037
    backendId: mcpBackend.name
    mcpProperties: {
      transportType: 'streamable'
    }
  }
}

resource mcpPolicy 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = {
  parent: mcpApi
  name: 'policy'
  dependsOn: [nv]
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/mcp-api.xml')
  }
}

// ---------- A2A API: specialist agent (preview; properties not yet in the published schema) ----------

resource a2aApi 'Microsoft.ApiManagement/service/apis@2025-09-01-preview' = {
  parent: apim
  name: 'specialist-agent'
  properties: {
    type: 'a2a'
    displayName: 'Specialist agent (A2A)'
    description: 'Well-Architected reviewer agent exposed over A2A'
    path: 'specialist'
    protocols: ['https']
    subscriptionRequired: false
    #disable-next-line BCP037
    isAgent: true
    #disable-next-line BCP037
    agent: { id: 'specialist-agent' }
    #disable-next-line BCP089
    a2aProperties: {
      agentCardPath: '/.well-known/agent-card.json'
      agentCardBackendUrl: '${specialistUrl}/.well-known/agent-card.json'
    }
    #disable-next-line BCP037
    jsonRpcProperties: {
      backendUrl: specialistUrl
      path: '/'
    }
  }
}

resource a2aPolicy 'Microsoft.ApiManagement/service/apis/policies@2025-09-01-preview' = {
  parent: a2aApi
  name: 'policy'
  dependsOn: [nv, nvGatewayKey]
  properties: {
    format: 'rawxml'
    value: loadTextContent('../policies/a2a-api.xml')
  }
}

output name string = apim.name
output gatewayUrl string = apim.properties.gatewayUrl
output principalId string = apim.identity.principalId
