#!/usr/bin/env bash
# Rebuild one agent image in ACR and roll its container app to it (fast inner loop after code changes).
#   scripts/update-agent.sh orchestrator|specialist
set -euo pipefail
cd "$(dirname "$0")/.."
agent="$1"
RG="${RG:-rg-apim-ai-demo}"
TAG="${TAG:-$(date +%Y%m%d%H%M%S)}"
acr=$(az acr list -g "$RG" --query "[0].name" -o tsv)
az acr build -r "$acr" -t "$agent-agent:$TAG" -f "agents/$agent/Dockerfile" agents --no-logs -o none
az containerapp update -n "ca-$agent" -g "$RG" --image "$acr.azurecr.io/$agent-agent:$TAG" -o none
echo "ca-$agent now runs $agent-agent:$TAG"
