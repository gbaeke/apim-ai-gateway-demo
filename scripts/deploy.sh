#!/usr/bin/env bash
# End-to-end deployment:
#   1. infrastructure (first run without container apps, because the images don't exist yet)
#   2. build both agent images in ACR (cloud build, no local Docker needed)
#   3. infrastructure again, now with the container apps
#   4. register both agents as external agents in Foundry
set -euo pipefail
cd "$(dirname "$0")/.."

RG="${RG:-rg-apim-ai-demo}"
LOCATION="${LOCATION:-francecentral}"
TAG="${TAG:-$(date +%Y%m%d%H%M%S)}"
# AI_GATEWAY_TIER=1 also deploys an empty AI Gateway tier (preview) instance in AI_GATEWAY_LOCATION.
AI_GATEWAY_TIER="${AI_GATEWAY_TIER:-0}"
AI_GATEWAY_LOCATION="${AI_GATEWAY_LOCATION:-swedencentral}"

for tool in az uv python3 openssl; do
  command -v "$tool" >/dev/null || { echo "Missing prerequisite: $tool"; exit 1; }
done
az account show -o none 2>/dev/null || { echo "Run 'az login' first."; exit 1; }
echo "Deploying to subscription '$(az account show --query name -o tsv)', resource group $RG ($LOCATION)"

az group create -n "$RG" -l "$LOCATION" --tags purpose=apim-ai-gateway-demo -o none
publisher_email=$(az account show --query user.name -o tsv)
deployer_id=$(az ad signed-in-user show --query id -o tsv)
# Reuse the gateway key the specialist already has, so APIM and the agent never disagree mid-rollout.
gateway_key=$(az containerapp secret show -n ca-specialist -g "$RG" --secret-name gateway-key --query value -o tsv 2>/dev/null || true)
gateway_key="${gateway_key:-$(openssl rand -hex 32)}"

deploy() {
  az deployment group create -g "$RG" -n main -f infra/main.bicep \
    -p publisherEmail="$publisher_email" deployerPrincipalId="$deployer_id" specialistGatewayKey="$gateway_key" \
    deployAiGatewayTier="$([[ $AI_GATEWAY_TIER == 1 ]] && echo true || echo false)" aiGatewayLocation="$AI_GATEWAY_LOCATION" "$@" \
    --query properties.outputs -o json > .deploy-outputs.json
}
out() { python3 -c "import json,sys; print(json.load(open('.deploy-outputs.json'))['$1']['value'])"; }

if [[ "${SKIP_INFRA:-}" != "1" ]]; then
  echo "==> [1/4] Infrastructure"
  acr=$(az acr list -g "$RG" --query "[0].name" -o tsv 2>/dev/null || true)
  if [[ -z "$acr" ]]; then deploy deployApps=false; fi
fi

acr=$(az acr list -g "$RG" --query "[0].name" -o tsv)
echo "==> [2/4] Building images in $acr (tag $TAG)"
pids=()
for agent in specialist orchestrator; do
  az acr build -r "$acr" -t "$agent-agent:$TAG" -f "agents/$agent/Dockerfile" agents --no-logs -o none &
  pids+=($!)
done
for pid in "${pids[@]}"; do wait "$pid"; done

echo "==> [3/4] Infrastructure + container apps"
deploy deployApps=true imageTag="$TAG"

echo "==> [4/4] Registering external agents in Foundry"
projects=$(python3 -c "import json; print(','.join(json.load(open('.deploy-outputs.json'))['foundryProjectEndpoints']['value']))")
# On a fresh install the deployer's Foundry role assignment can take a minute or two to propagate.
for attempt in 1 2 3 4 5; do
  uv run -q --project scripts/register python scripts/register/register_agents.py --projects "$projects" && break
  [[ $attempt == 5 ]] && { echo "Registration failed; rerun: SKIP_INFRA=1 $0"; exit 1; }
  echo "Retrying registration in 60s (role assignment propagation)..."; sleep 60
done

echo
RG="$RG" scripts/show-endpoints.sh
