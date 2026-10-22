#!/usr/bin/env bash
# Change an APIM named value live (policies pick it up within seconds), e.g. during a demo:
#   scripts/set-policy-knob.sh specialist-tpm 300        # throttle the specialist's model budget
#   scripts/set-policy-knob.sh mcp-calls-per-minute 2    # throttle MCP tool calls
#   scripts/set-policy-knob.sh specialist-tpm 20000      # back to normal
#   scripts/set-policy-knob.sh mcp-blocked-tools microsoft_docs_search   # block one MCP tool
#   scripts/set-policy-knob.sh a2a-allowed-callers none  # nobody may delegate to the specialist
set -euo pipefail
RG="${RG:-rg-apim-ai-demo}"
apim=$(az apim list -g "$RG" --query "[0].name" -o tsv)
az apim nv update -g "$RG" --service-name "$apim" --named-value-id "$1" --value "$2" -o none
echo "$apim: $1 = $2"
