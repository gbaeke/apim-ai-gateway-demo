#!/usr/bin/env bash
# Interactive menu to turn the APIM policy knobs (named values) live during a demo.
# Policies pick up a change within seconds. Non-interactive: scripts/set-policy-knob.sh.
set -euo pipefail
RG="${RG:-rg-apim-ai-demo}"

# name | default (keep in sync with infra/modules/apim.bicep) | demo presets | what it does
KNOBS=(
  "orchestrator-tpm|50000|300|Orchestrator model tokens per minute"
  "specialist-tpm|20000|300|Specialist model tokens per minute"
  "mcp-calls-per-minute|300|2|MCP requests per minute per agent (~5 per tool call)"
  "a2a-calls-per-minute|20|1|A2A requests per minute per agent"
  "mcp-blocked-tools|none|microsoft_docs_search microsoft_docs_fetch microsoft_code_sample_search|MCP tools APIM refuses"
  "mcp-allowed-callers|orchestrator-agent|none|Agents allowed on the MCP server"
  "a2a-allowed-callers|orchestrator-agent|none|Agents allowed to delegate over A2A"
)

apim=$(az apim list -g "$RG" --query "[0].name" -o tsv)
[[ -n "$apim" ]] || { echo "No API Management instance in $RG (set RG=...)"; exit 1; }

field() { cut -d'|' -f"$2" <<<"$1"; }
set_knob() {
  az apim nv update -g "$RG" --service-name "$apim" --named-value-id "$1" --value "$2" -o none
  echo "  $1 = $2"
}

while true; do
  echo
  echo "APIM policy knobs on $apim ($RG)"
  current=$(az apim nv list -g "$RG" --service-name "$apim" --query "[].[name, value]" -o tsv)
  for i in "${!KNOBS[@]}"; do
    name=$(field "${KNOBS[$i]}" 1); default=$(field "${KNOBS[$i]}" 2)
    value=$(awk -F'\t' -v n="$name" '$1 == n { print $2 }' <<<"$current")
    marker=$([[ "$value" == "$default" ]] && echo " " || echo "*")
    printf "  %d) %s %-22s %-24s %s\n" $((i + 1)) "$marker" "$name" "${value:-<missing>}" "$(field "${KNOBS[$i]}" 4)"
  done
  echo "  r) reset all to defaults    q) quit        (* = changed from default)"
  read -rp "> " choice

  case "$choice" in
    q|Q|"") exit 0 ;;
    r|R)
      for knob in "${KNOBS[@]}"; do set_knob "$(field "$knob" 1)" "$(field "$knob" 2)"; done
      continue ;;
  esac
  if ! [[ "$choice" =~ ^[0-9]+$ ]] || (( choice < 1 || choice > ${#KNOBS[@]} )); then
    echo "  Unknown choice: $choice"; continue
  fi

  knob=${KNOBS[$((choice - 1))]}
  name=$(field "$knob" 1); default=$(field "$knob" 2)
  read -ra options <<<"$default $(field "$knob" 3)"
  echo "  $name:"
  for j in "${!options[@]}"; do
    printf "    %d) %s%s\n" $((j + 1)) "${options[$j]}" "$([[ $j == 0 ]] && echo "  (default)")"
  done
  echo "    or type any value (comma-separated lists are fine); empty cancels"
  read -rp "  > " pick
  [[ -n "$pick" ]] || continue
  if [[ "$pick" =~ ^[0-9]+$ ]] && (( pick >= 1 && pick <= ${#options[@]} )); then
    pick=${options[$((pick - 1))]}
  fi
  set_knob "$name" "$pick"
done
