# APIM AI Gateway demo: agents, MCP and A2A behind Azure API Management

Two LangChain Deep Agents run on Azure Container Apps. Every call they make goes through Azure API
Management (Basic v2): calls to the model, to an MCP server, and to each other over A2A. APIM checks
the calling agent's managed identity and applies that agent's policy, then logs everything.

| Route on APIM | Backend | Orchestrator | Specialist |
| --- | --- | --- | --- |
| `/foundry/openai/v1` (LLM API) | Foundry account with `gpt-6-luna` | 50K tokens/min | 20K tokens/min |
| `/mslearn/mcp` (MCP passthrough) | Microsoft Learn MCP server | allowed | 403 |
| `/specialist` (A2A agent API, preview) | Specialist agent | allowed | 403 |

- **Orchestrator** (*Azure Architect Copilot*): a chat UI. It answers from Microsoft Learn (MCP) and delegates design reviews to the specialist (A2A).
- **Specialist** (*Well-Architected Reviewer*): an A2A server with no UI.

Observability is split on purpose:

- **Gateway:** `log-apim-*` and `appi-apim-*` hold gateway logs, prompt/completion logs, MCP logs, and token metrics per calling agent.
- **Agents:** `log-agents-*` and `appi-agents-*` hold OpenTelemetry GenAI spans. They're connected to one Foundry project per agent (account `aif-agents-*`), where each agent is registered as an *external agent*.

## Repository layout

```
infra/
  main.bicep                 entry point (resource group scope)
  modules/                   monitoring, foundry-models, foundry-agents, container-env, apim, container-apps
  policies/                  global.xml (identity), llm-api.xml, mcp-api.xml, a2a-api.xml
  queries.kql                KQL used in the demo
agents/
  common/aigateway/          managed identity tokens, model client, gateway activity hooks, telemetry
  orchestrator/              Deep Agent + MCP tools + A2A tool, FastAPI SSE API, web UI (static/)
  specialist/                Deep Agent + waf_checklist tool, A2A JSON-RPC server
scripts/
  deploy.sh                  install or update everything
  destroy.sh                 remove everything (incl. purging soft-deleted resources)
  show-endpoints.sh          print the UI, gateway and Foundry entry points of a deployment
  set-policy-knob.sh         change an APIM named value live (token budgets, rate limits)
  update-agent.sh            rebuild and roll one agent after a code change
  register/                  registers the agents as external agents in Foundry
```

## Install

### Prerequisites

- An Azure subscription where you can create resources **and role assignments**: *Owner*, or *Contributor* plus *User Access Administrator*.
- Quota for `gpt-6-luna` (GlobalStandard) in the target region. The default deployment uses 300K tokens/min. Check it with:
  `az cognitiveservices usage list -l francecentral -o table | grep gpt-6-luna`
- Tools: [Azure CLI](https://learn.microsoft.com/cli/azure/install-azure-cli) (tested with 2.90) with Bicep (`az bicep install`), [uv](https://docs.astral.sh/uv/), `python3` and `openssl`. You don't need Docker locally, because images are built in the cloud with `az acr build`.
- No Entra app registration is needed.

### Deploy

```bash
az login
az account set -s <subscription>
./scripts/deploy.sh
```

Defaults are resource group `rg-apim-ai-demo` in `francecentral`. Override them with environment variables, for example `RG=my-rg LOCATION=swedencentral ./scripts/deploy.sh`.

The first run takes about 20–30 minutes, most of it creating API Management. `deploy.sh`:

1. deploys the infrastructure without the container apps, because the images don't exist yet;
2. builds both agent images in the container registry;
3. deploys again, now including the container apps;
4. registers both agents as external agents in their Foundry projects.

At the end it prints the endpoints. Print them again at any time with `./scripts/show-endpoints.sh`.

Run `deploy.sh` again to apply any change; it is idempotent. For code-only changes, `./scripts/update-agent.sh orchestrator` (or `specialist`) is faster.

### Cost and exposure

The deployment creates billable resources. The biggest costs are API Management Basic v2 (hourly) and model tokens.

The orchestrator UI has a public URL and **no sign-in**, so anyone with the URL can use it, within its token budget. Remove the deployment when you're done, or add Container Apps built-in authentication.

## Remove

```bash
./scripts/destroy.sh          # lists the resources and asks you to type the resource group name
FORCE=1 ./scripts/destroy.sh  # no prompt
```

The script deletes the resource group. It then **purges** the soft-deleted Foundry accounts and the API Management instance; without that step, their names stay reserved for days and a redeploy into the same resource group fails. Use `RG=my-rg` if you deployed under another name.

## Running the demo

About 15 minutes. Get the URLs with `./scripts/show-endpoints.sh`.

1. **The architecture.** Open the resource group in the Azure portal. The two agents (Container Apps, each with its own managed identity) can reach the model, the MCP server and each other only through APIM.

2. **A grounded answer (model + MCP).** In the orchestrator UI, click *Token limit vs rate limit in APIM*. In the *Gateway activity* panel:
   - each Model and MCP call is shown with caller `orchestrator-agent`, set by APIM after it validated the managed identity;
   - the token-budget bar comes from APIM's `x-ratelimit-remaining-tokens` header;
   - dimmed MCP `GET`/`DELETE` 405s are normal. The stateless Learn server refuses those optional streamable-HTTP calls.

3. **Delegation over A2A.** Click *Review this demo's own architecture*. The A2A rows show the agent card fetched from `/specialist/.well-known/agent-card.json` and the JSON-RPC call to `/specialist/`. APIM rewrites the card's address, so the orchestrator never learns the specialist's real URL.

4. **Identity is the key.** In APIM → APIs:
   - The **All APIs** policy validates the token and maps the caller's client ID to an agent name (`infra/policies/global.xml`).
   - The MCP server and A2A API policies allow only `orchestrator-agent`. The specialist gets a 403 there, and any other token (for example your own Azure CLI token) gets a 401.

5. **Change policy live.** Throttle the specialist and ask for another review. APIM returns 429 to the specialist's model calls, the A2A task fails with that reason, and the orchestrator explains it. Then restore the budget.
   ```bash
   ./scripts/set-policy-knob.sh specialist-tpm 300
   ./scripts/set-policy-knob.sh specialist-tpm 20000   # restore
   ```
   The other knobs work the same way: `orchestrator-tpm`, `mcp-calls-per-minute` and `a2a-calls-per-minute`.

6. **Gateway observability.**
   - APIM → Monitoring → **Analytics → Language models**: token usage at a glance.
   - Log Analytics workspace `log-apim-*`: run the queries in `infra/queries.kql`. They show which agent called which API and what APIM decided (200, 401, 403, 429); prompts and completions (`ApiManagementGatewayLlmLog`); MCP tool calls (`ApiManagementGatewayMCPLog`); and tokens per agent.

7. **Agent observability in Foundry.** Open the Foundry portal (ai.azure.com) → the `aif-agents-*` account → project `orchestrator-agent` → **Build → Agents → orchestrator-agent → Traces**.
   - Always enter through the Agents list. External agents live under a `/build/agents/external/…` URL; the generic agent URL shows an empty page.
   - Search for `POST /api/chat` and open a trace. One trajectory shows the orchestrator's `invoke_agent`, `chat` and `execute_tool` spans, its calls through APIM, and the specialist's spans nested inside, because W3C trace context flows through the gateway.
   - Spans take 2–5 minutes to appear.

Talking point: Deep Agents' built-in *general-purpose* subagent runs inside the orchestrator's process, so APIM never sees it. The specialist is a separate workload with its own identity, token budget and rights, governed by the gateway.

## Design notes

### Identity without an app registration

Agents request tokens for `https://cognitiveservices.azure.com` with their user-assigned managed identity. APIM's global policy validates three things:
- the issuer (your tenant);
- the audience;
- the caller's client ID (`client-application-ids`), which acts as the allow-list.

The agent identities have **no** role on the model's Foundry account, so the token is useless anywhere except at the gateway. APIM then:
- calls Foundry with its own managed identity;
- strips the token before calling Microsoft Learn;
- replaces the token with a gateway key that the specialist requires, so calling the specialist directly gets a 403.

If you can create an app registration, a dedicated audience with an app role per capability is cleaner. Validate it with `required-claims`.

### A2A and the agent card

The APIM A2A agent API (preview) rewrites the agent card's top-level `url` to the gateway address. That is an A2A 0.3 field. The specialist therefore publishes its card in 0.3 shape with its own address, and accepts 0.3 JSON-RPC (`enable_v0_3_compat`).

The orchestrator fetches the card from APIM and follows the rewritten URL. The specialist calls the model through APIM like any agent, but it never needs to know its own published gateway address.

### Tracing

Each agent exports OpenTelemetry to `appi-agents-*`. `langchain-azure-ai` emits the GenAI spans, and every span is tagged with `gen_ai.agent.id`; Foundry uses that attribute to match traces to the registered external agent. The health-probe endpoint is excluded from tracing.
