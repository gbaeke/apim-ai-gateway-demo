"""Orchestrator: an Azure architect assistant built with LangChain Deep Agents.

All three of its dependencies go through APIM, authenticated with the agent's managed identity:
  model       -> {gateway}/foundry/openai/v1        (LLM API)
  docs tools  -> {gateway}/mslearn/mcp              (Microsoft Learn MCP server)
  specialist  -> {gateway}/specialist               (A2A agent API)
"""

import logging
import os

import httpx
from a2a.client import A2ACardResolver, A2AClientError, ClientConfig, create_client
from a2a.helpers import get_artifact_text, get_message_text, new_text_message
from a2a.types import Role, SendMessageRequest, TaskState
from deepagents import create_deep_agent
from langchain_core.tools import tool
from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import InMemorySaver

from aigateway import activity
from aigateway.auth import GatewayAuth
from aigateway.model import chat_model

log = logging.getLogger(__name__)

MCP_URL = os.environ["MCP_URL"]
SPECIALIST_A2A_URL = os.environ["SPECIALIST_A2A_URL"].rstrip("/")
TOOL_NAMES: list[str] = []


@tool
async def ask_well_architected_reviewer(solution_description: str) -> str:
    """Delegate a Well-Architected Framework review to the specialist agent (over A2A).

    Use this when the user wants an architecture reviewed, assessed or hardened.

    Args:
        solution_description: a self-contained description of the solution and what to focus on.
    """
    try:
        async with activity.async_client(auth=GatewayAuth(), timeout=300.0) as http:
            card = await A2ACardResolver(httpx_client=http, base_url=SPECIALIST_A2A_URL).get_agent_card()
            client = await create_client(card, client_config=ClientConfig(streaming=False, httpx_client=http))
            request = SendMessageRequest(message=new_text_message(solution_description, role=Role.ROLE_USER))
            parts: list[str] = []
            async for event in client.send_message(request):
                if event.HasField("task"):
                    parts = [get_artifact_text(a) for a in event.task.artifacts] or parts
                    if event.task.status.state == TaskState.TASK_STATE_FAILED:
                        parts = [get_message_text(event.task.status.message) or "The review task failed."]
                elif event.HasField("message"):
                    parts = [get_message_text(event.message)]
                elif event.HasField("artifact_update"):
                    parts.append(get_artifact_text(event.artifact_update.artifact))
            await client.close()
    except (httpx.HTTPError, A2AClientError) as exc:
        # Let the model explain the failure (for example an APIM 403 or 429) instead of aborting the turn.
        log.warning("Specialist call failed: %s", exc)
        return f"The Well-Architected reviewer could not be reached through the gateway: {exc}"
    return "\n\n".join(p for p in parts if p) or "The reviewer returned no content."


SYSTEM_PROMPT = """You are **Azure Architect Copilot**, an assistant for cloud architects.

Always ground Azure facts in Microsoft Learn: call `microsoft_docs_search` before answering any
question about Azure services, features, limits, tiers or pricing, even when you think you know
the answer. Never answer such a question from memory alone.

Tools:
- Microsoft Learn MCP tools (`microsoft_docs_search`, `microsoft_docs_fetch`,
  `microsoft_code_sample_search`): ground every factual claim about Azure in official docs.
  Search first; fetch a page only when you need detail.
- `ask_well_architected_reviewer`: a specialist agent reached over A2A. Use it whenever the user
  asks for a review, an assessment, risks or improvements of a design. Send it a self-contained
  description of the design.

Plan multi-step work with your todo list. Answer in clear Markdown, keep it concise, and cite
the Learn pages you used as links."""


async def build_agent():
    mcp = MultiServerMCPClient(
        {
            "mslearn": {
                "transport": "streamable_http",
                "url": MCP_URL,
                "auth": GatewayAuth(),
                "httpx_client_factory": activity.mcp_client_factory,
            }
        }
    )
    mcp_tools = await mcp.get_tools()
    log.info("Loaded %d MCP tools via APIM: %s", len(mcp_tools), [t.name for t in mcp_tools])

    global TOOL_NAMES
    TOOL_NAMES = [t.name for t in mcp_tools] + [ask_well_architected_reviewer.name]
    return create_deep_agent(
        model=chat_model(),
        tools=[*mcp_tools, ask_well_architected_reviewer],
        system_prompt=SYSTEM_PROMPT,
        checkpointer=InMemorySaver(),
        name="orchestrator-agent",
    )
