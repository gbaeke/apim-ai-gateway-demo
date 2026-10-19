#!/usr/bin/env bash
# Prints the demo's endpoints and portal entry points for the current deployment.
set -euo pipefail
RG="${RG:-rg-apim-ai-demo}"

apim=$(az apim list -g "$RG" --query "[0].name" -o tsv)
gateway=$(az apim show -g "$RG" -n "$apim" --query gatewayUrl -o tsv)
ui=$(az containerapp show -g "$RG" -n ca-orchestrator --query properties.configuration.ingress.fqdn -o tsv 2>/dev/null)
agents=$(az cognitiveservices account list -g "$RG" --query "[?properties.allowProjectManagement].name | [0]" -o tsv)

cat <<EOF
Orchestrator UI    https://$ui
APIM gateway       $gateway
  model route      $gateway/foundry/openai/v1
  MCP route        $gateway/mslearn/mcp
  A2A agent card   $gateway/specialist/.well-known/agent-card.json
Foundry portal     https://ai.azure.com  ->  account $agents  ->  project orchestrator-agent | specialist-agent
                   ->  Build > Agents > (agent) > Traces
EOF
