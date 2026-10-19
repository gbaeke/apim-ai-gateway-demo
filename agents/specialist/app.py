"""Specialist agent served over A2A (JSON-RPC). No UI.

Only APIM may call it: requests must carry the gateway key that APIM injects (the agent tokens of
callers are validated by APIM and stripped before they get here).
"""

import hmac
import logging
import os

from aigateway import telemetry

# Console logging first: configure_azure_monitor adds a root handler, which would turn basicConfig into a no-op.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
telemetry.setup()  # before any httpx client exists

from a2a.helpers import new_task_from_user_message, new_text_part  # noqa: E402
from a2a.server.agent_execution import AgentExecutor, RequestContext  # noqa: E402
from a2a.server.events import EventQueue  # noqa: E402
from a2a.server.request_handlers import DefaultRequestHandler  # noqa: E402
from a2a.compat.v0_3.conversions import to_compat_agent_card  # noqa: E402
from a2a.server.routes import add_a2a_routes_to_fastapi, create_jsonrpc_routes  # noqa: E402
from a2a.server.tasks import InMemoryTaskStore, TaskUpdater  # noqa: E402
from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentProvider, AgentSkill  # noqa: E402
from fastapi import FastAPI, Request  # noqa: E402
from fastapi.responses import JSONResponse  # noqa: E402
from langchain_core.messages import AIMessage  # noqa: E402

from agent import build_agent  # noqa: E402

log = logging.getLogger("specialist")

PUBLIC_URL = os.environ.get("PUBLIC_URL", "http://localhost:8001").rstrip("/")
GATEWAY_KEY = os.environ.get("GATEWAY_KEY", "")

agent = build_agent()

AGENT_CARD = AgentCard(
    name="Well-Architected Reviewer",
    description="Reviews Azure solution designs against the Azure Well-Architected Framework pillars.",
    version="1.0.0",
    provider=AgentProvider(organization="APIM AI Gateway demo", url=PUBLIC_URL),
    supported_interfaces=[AgentInterface(protocol_binding="JSONRPC", url=PUBLIC_URL, protocol_version="0.3")],
    capabilities=AgentCapabilities(streaming=False),
    default_input_modes=["text/plain"],
    default_output_modes=["text/markdown"],
    skills=[
        AgentSkill(
            id="waf-review",
            name="Well-Architected review",
            description="Assess an Azure architecture for reliability, security, cost, operations and performance.",
            tags=["azure", "architecture", "well-architected"],
            examples=["Review: two Container Apps calling Azure OpenAI through API Management Basic v2."],
        )
    ],
)


def _text(message) -> str:
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content if isinstance(part, dict))


class ReviewerExecutor(AgentExecutor):
    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        task = context.current_task or new_task_from_user_message(context.message)
        if context.current_task is None:
            await event_queue.enqueue_event(task)
        updater = TaskUpdater(event_queue, task.id, task.context_id)
        await updater.start_work()

        question = context.get_user_input()
        log.info("Review requested (context %s): %s", task.context_id, question[:120])
        try:
            result = await agent.ainvoke(
                {"messages": [{"role": "user", "content": question}]},
                config={
                    "callbacks": telemetry.langchain_callbacks(),
                    "metadata": {"a2a.context_id": task.context_id, "a2a.task_id": task.id},
                    "recursion_limit": 40,
                },
            )
        except Exception as exc:  # e.g. APIM throttling this agent's model budget (429)
            log.warning("Review failed: %s", exc)
            status = getattr(exc, "status_code", None)
            reason = "the gateway throttled my model access (429)" if status == 429 else str(exc)[:300]
            await updater.failed(updater.new_agent_message([new_text_part(f"Review failed: {reason}")]))
            return
        answer = next((_text(m) for m in reversed(result["messages"]) if isinstance(m, AIMessage) and _text(m)), "")
        await updater.add_artifact(parts=[new_text_part(answer or "No review produced.")], name="review")
        await updater.complete()

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        raise NotImplementedError("cancel is not supported")


handler = DefaultRequestHandler(agent_executor=ReviewerExecutor(), task_store=InMemoryTaskStore(), agent_card=AGENT_CARD)

app = FastAPI(title="Specialist agent (A2A)", docs_url=None, redoc_url=None)
add_a2a_routes_to_fastapi(
    app,
    jsonrpc_routes=create_jsonrpc_routes(handler, rpc_url="/", enable_v0_3_compat=True),
)

# The card is published in A2A 0.3 shape (top-level `url` + `preferredTransport`): that is what the
# APIM A2A agent API (preview) rewrites, so callers that fetch the card through APIM get the gateway
# address and the agent itself never needs to know the gateway exists. 1.x clients still read it.
AGENT_CARD_V03 = to_compat_agent_card(AGENT_CARD).model_dump(mode="json", by_alias=True, exclude_none=True)


@app.get("/.well-known/agent-card.json", include_in_schema=False)
@app.get("/.well-known/agent.json", include_in_schema=False)
async def agent_card():
    return JSONResponse(AGENT_CARD_V03)




@app.middleware("http")
async def require_gateway(request: Request, call_next):
    if request.url.path == "/healthz" or not GATEWAY_KEY:
        return await call_next(request)
    if not hmac.compare_digest(request.headers.get("x-gateway-key", ""), GATEWAY_KEY):
        return JSONResponse({"error": "Call this agent through the API Management gateway"}, status_code=403)
    return await call_next(request)


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}
