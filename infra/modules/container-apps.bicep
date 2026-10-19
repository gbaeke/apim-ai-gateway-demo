// The two agents. Each agent runs under its own user-assigned identity:
// that identity pulls the image from ACR and is the identity APIM validates on every call.
param location string
param tags object
param envName string
param acrLoginServer string
param apimGatewayUrl string
param modelName string
@secure()
param agentsAppInsightsConnectionString string
param orchestratorIdentityId string
param orchestratorClientId string
param specialistIdentityId string
param specialistClientId string
@secure()
param specialistGatewayKey string
param orchestratorImage string
param specialistImage string

resource env 'Microsoft.App/managedEnvironments@2026-01-01' existing = {
  name: envName
}

var commonEnv = [
  { name: 'APIM_GATEWAY_URL', value: apimGatewayUrl }
  { name: 'MODEL_DEPLOYMENT', value: modelName }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', secretRef: 'appinsights-connection-string' }
  { name: 'AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED', value: 'true' }
]

resource specialist 'Microsoft.App/containerApps@2026-01-01' = {
  name: 'ca-specialist'
  location: location
  tags: union(tags, { 'azd-service-name': 'specialist' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${specialistIdentityId}': {} }
  }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [{ server: acrLoginServer, identity: specialistIdentityId }]
      secrets: [
        { name: 'appinsights-connection-string', value: agentsAppInsightsConnectionString }
        { name: 'gateway-key', value: specialistGatewayKey }
      ]
    }
    template: {
      containers: [
        {
          name: 'specialist'
          image: specialistImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: concat(commonEnv, [
            { name: 'AZURE_CLIENT_ID', value: specialistClientId }
            { name: 'OTEL_SERVICE_NAME', value: 'specialist-agent' }
            { name: 'AGENT_ID', value: 'specialist-agent' }
            { name: 'GATEWAY_KEY', secretRef: 'gateway-key' }
            // The agent's own address; APIM rewrites the card for callers that come through the gateway.
            { name: 'PUBLIC_URL', value: 'https://ca-specialist.${env.properties.defaultDomain}' }
          ])
          probes: [
            { type: 'Readiness', httpGet: { path: '/healthz', port: 8000 } }
          ]
        }
      ]
      scale: { minReplicas: 1, maxReplicas: 3 }
    }
  }
}

resource orchestrator 'Microsoft.App/containerApps@2026-01-01' = {
  name: 'ca-orchestrator'
  location: location
  tags: union(tags, { 'azd-service-name': 'orchestrator' })
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: { '${orchestratorIdentityId}': {} }
  }
  properties: {
    environmentId: env.id
    workloadProfileName: 'Consumption'
    configuration: {
      ingress: {
        external: true
        targetPort: 8000
        transport: 'auto'
      }
      registries: [{ server: acrLoginServer, identity: orchestratorIdentityId }]
      secrets: [
        { name: 'appinsights-connection-string', value: agentsAppInsightsConnectionString }
      ]
    }
    template: {
      containers: [
        {
          name: 'orchestrator'
          image: orchestratorImage
          resources: { cpu: json('1.0'), memory: '2Gi' }
          env: concat(commonEnv, [
            { name: 'AZURE_CLIENT_ID', value: orchestratorClientId }
            { name: 'OTEL_SERVICE_NAME', value: 'orchestrator-agent' }
            { name: 'AGENT_ID', value: 'orchestrator-agent' }
            { name: 'MCP_URL', value: '${apimGatewayUrl}/mslearn/mcp' }
            { name: 'SPECIALIST_A2A_URL', value: '${apimGatewayUrl}/specialist' }
          ])
          probes: [
            { type: 'Readiness', httpGet: { path: '/healthz', port: 8000 } }
          ]
        }
      ]
      // The UI keeps conversations in memory, so keep a single replica.
      scale: { minReplicas: 1, maxReplicas: 1 }
    }
  }
}

output orchestratorUrl string = 'https://${orchestrator.properties.configuration.ingress.fqdn}'
output specialistUrl string = 'https://${specialist.properties.configuration.ingress.fqdn}'
