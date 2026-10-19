"""Register each Container Apps agent as an *external agent* in its own Foundry project.

Registration is metadata only: Foundry shows traces from the project's Application Insights whose
spans carry ``gen_ai.agent.id == otel_agent_id`` (the agents set it via AGENT_ID). The project name
equals the agent id, so one endpoint per project is all this needs.
"""

import argparse

from azure.ai.projects import AIProjectClient
from azure.ai.projects.models import ExternalAgentDefinition
from azure.core.exceptions import ResourceNotFoundError
from azure.identity import AzureCliCredential

DESCRIPTIONS = {
    "orchestrator-agent": "Azure Architect Copilot (LangChain Deep Agent on Azure Container Apps). "
    "Calls gpt-6-luna, Microsoft Learn MCP and the specialist agent through APIM.",
    "specialist-agent": "Well-Architected Reviewer (LangChain Deep Agent on Azure Container Apps), "
    "served over A2A behind APIM.",
}


def register(endpoint: str, credential) -> None:
    agent_id = endpoint.rstrip("/").rsplit("/", 1)[-1]
    client = AIProjectClient(endpoint=endpoint, credential=credential, allow_preview=True)
    try:
        client.agents.get(agent_id)
        print(f"{agent_id}: already registered")
        return
    except ResourceNotFoundError:
        pass
    version = client.agents.create_version(
        agent_name=agent_id,
        description=DESCRIPTIONS.get(agent_id, agent_id),
        definition=ExternalAgentDefinition(otel_agent_id=agent_id),
        metadata={"hosting": "azure-container-apps", "gateway": "azure-api-management"},
    )
    print(f"{agent_id}: registered (version {version.version})")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--projects", required=True, help="Comma-separated Foundry project endpoints")
    args = parser.parse_args()
    credential = AzureCliCredential()
    for endpoint in args.projects.split(","):
        register(endpoint, credential)


if __name__ == "__main__":
    main()
