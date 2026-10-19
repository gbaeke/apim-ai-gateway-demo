// Container Apps environment; logs go to the agents' Log Analytics workspace.
param location string
param suffix string
param tags object
param lawName string

resource law 'Microsoft.OperationalInsights/workspaces@2025-02-01' existing = {
  name: lawName
}

resource env 'Microsoft.App/managedEnvironments@2026-01-01' = {
  name: 'cae-${suffix}'
  location: location
  tags: tags
  properties: {
    appLogsConfiguration: {
      destination: 'log-analytics'
      logAnalyticsConfiguration: {
        customerId: law.properties.customerId
        sharedKey: law.listKeys().primarySharedKey
      }
    }
    workloadProfiles: [{ name: 'Consumption', workloadProfileType: 'Consumption' }]
  }
}

output name string = env.name
output defaultDomain string = env.properties.defaultDomain
