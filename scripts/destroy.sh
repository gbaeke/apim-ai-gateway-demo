#!/usr/bin/env bash
# Removes everything the demo deployed:
#   1. deletes the resource group (all resources in it)
#   2. purges the soft-deleted Foundry accounts and API Management instance, so their names are free
#      again and a redeploy into the same resource group works straight away
#
#   scripts/destroy.sh            # asks for confirmation
#   FORCE=1 scripts/destroy.sh    # no prompt
set -euo pipefail

RG="${RG:-rg-apim-ai-demo}"

if [[ "$(az group exists -n "$RG")" != "true" ]]; then
  echo "Resource group $RG does not exist; checking for soft-deleted resources only."
  cog_accounts=""
  apim_services=""
else
  echo "Resources in $RG:"
  az resource list -g "$RG" --query "[].{name:name, type:type}" -o table
  # Remember what has to be purged after the delete (names + regions).
  cog_accounts=$(az cognitiveservices account list -g "$RG" --query "[].[name, location]" -o tsv)
  apim_services=$(az apim list -g "$RG" --query "[].[name, location]" -o tsv)

  if [[ "${FORCE:-}" != "1" ]]; then
    read -r -p "Type the resource group name to delete it permanently: " answer
    [[ "$answer" == "$RG" ]] || { echo "Aborted."; exit 1; }
  fi

  echo "==> Deleting resource group $RG (takes several minutes)"
  az group delete -n "$RG" --yes
fi

echo "==> Purging soft-deleted Foundry accounts"
while read -r name location; do
  [[ -z "$name" ]] && continue
  az cognitiveservices account purge -g "$RG" -n "$name" -l "$location" && echo "purged $name"
done <<< "$cog_accounts"

echo "==> Purging soft-deleted API Management instances"
while read -r name location; do
  [[ -z "$name" ]] && continue
  az apim deletedservice purge -n "$name" -l "$location" && echo "purged $name"
done <<< "$apim_services"

rm -f "$(dirname "$0")/../.deploy-outputs.json"
echo "Done."
