#!/usr/bin/env bash
# Sends one random architecture question to the orchestrator, so Foundry's scheduled evaluations have
# fresh traces to score. Run it on a timer, e.g. every 5 minutes (see README).
#   ORCHESTRATOR_URL=https://ca-orchestrator... scripts/traffic.sh
# Without ORCHESTRATOR_URL it uses the URL from the last deploy (.deploy-outputs.json).
set -euo pipefail
cd "$(dirname "$0")/.."

url="${ORCHESTRATOR_URL:-$(python3 -c "import json; print(json.load(open('.deploy-outputs.json'))['orchestratorUrl']['value'])")}"

questions=(
  "What is the difference between the llm-token-limit and rate-limit-by-key policies in Azure API Management?"
  "Which Azure API Management tiers support MCP servers, and how do I expose an existing one?"
  "How should I load balance Azure OpenAI deployments across two regions with API Management?"
  "When should I use Azure Container Apps instead of AKS for a set of AI agents?"
  "How do managed identities authenticate a container app to Azure API Management?"
  "What are the options for private networking with Azure API Management Basic v2 and Standard v2?"
  "How do I log prompts and completions for Azure OpenAI calls that go through API Management?"
  "What does semantic caching in Azure API Management do, and what does it need?"
  "How do I set a monthly token quota per client for Azure OpenAI behind API Management?"
  "What is the recommended way to store secrets for an app on Azure Container Apps?"
  "How do I trace a request end to end across two agents on Azure with Application Insights?"
  "What are the limits of Azure Container Apps Consumption workload profiles?"
  "Review this design: a single VM runs a web app and SQL Server, backed up nightly to a storage account."
  "Review this design: an AKS cluster with public API server calls Azure OpenAI with an API key stored in a ConfigMap."
  "Review this design: a Function App behind API Management writes orders to Cosmos DB in one region."
  "Review this design: three agents on Container Apps share one managed identity and call each other directly over public ingress."
)
question="${questions[RANDOM % ${#questions[@]}]}"
body=$(python3 -c 'import json, sys; print(json.dumps({"message": sys.argv[1]}))' "$question")

# Read the whole stream: the turn (and its trace) runs until the "done" event.
summary=$(curl -sN --max-time 600 -X POST "$url/api/chat" -H 'Content-Type: application/json' -d "$body" | python3 -c '
import json, sys
event, tools, error = None, 0, None
for line in sys.stdin:
    if line.startswith("event: "):
        event = line[7:].strip()
    elif line.startswith("data: "):
        if event == "tool":
            tools += 1
        elif event == "error":
            error = json.loads(line[6:]).get("message")
print(f"error: {error}" if error else f"ok, {tools} tool calls")
')
echo "$(date -Is) ${summary:-no response} | $question"
