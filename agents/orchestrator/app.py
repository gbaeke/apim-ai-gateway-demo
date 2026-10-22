"""Orchestrator agent: conversational web UI + streaming chat API.

POST /api/chat streams Server-Sent Events:
  token     text produced by the model
  tool      a tool call starting (MCP docs tool, A2A delegation, or a Deep Agents built-in)
  result    a tool call finishing
  todos     the agent's current plan
  gateway   one call that went through APIM (kind, status, latency, caller, token budget)
  error     something failed (for example an APIM 429 or 403)
  done      end of the turn
"""

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from aigateway import telemetry

# Console logging first: configure_azure_monitor adds a root handler, which would turn basicConfig into a no-op.
logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
telemetry.setup()  # before any httpx client exists

from fastapi import FastAPI, HTTPException  # noqa: E402
from fastapi.responses import FileResponse, StreamingResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402
from langchain_core.messages import AIMessage, AIMessageChunk, ToolMessage  # noqa: E402
from pydantic import BaseModel  # noqa: E402

from aigateway import activity  # noqa: E402
from aigateway.config import settings  # noqa: E402
from aigateway.errors import gateway_error  # noqa: E402
import agent as agent_module  # noqa: E402
import knobs  # noqa: E402
from agent import build_agent  # noqa: E402

log = logging.getLogger("orchestrator")

STATIC = Path(__file__).parent / "static"
MCP_TOOL_PREFIX = "microsoft_"
A2A_TOOLS = {"ask_well_architected_reviewer"}

_agent = None
_agent_lock = asyncio.Lock()


async def get_agent():
    global _agent
    async with _agent_lock:
        if _agent is None:
            _agent = await build_agent()
    return _agent


@asynccontextmanager
async def lifespan(_: FastAPI):
    try:
        await get_agent()
    except Exception:  # keep serving the UI; the first chat retries
        log.exception("Agent initialisation failed; will retry on first request")
    yield


app = FastAPI(title="Azure Architect Copilot", lifespan=lifespan, docs_url=None, redoc_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(block.get("text", "") for block in content if isinstance(block, dict) and block.get("type") == "text")


def _via(tool_name: str) -> str:
    if tool_name.startswith(MCP_TOOL_PREFIX):
        return "mcp"
    if tool_name in A2A_TOOLS:
        return "a2a"
    return "local"


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"


def _describe_error(exc: Exception) -> dict:
    status, message = gateway_error(exc)
    return {"status": status, "message": message[:600]}


async def _run_turn(request: ChatRequest, conversation_id: str, queue: asyncio.Queue) -> None:
    """Runs one agent turn and pushes UI events onto ``queue``; gateway calls are captured too."""
    sink_token = activity.set_sink(lambda call: queue.put_nowait(("gateway", call)))
    try:
        agent = await get_agent()
        config = {
            "configurable": {"thread_id": conversation_id},
            "callbacks": telemetry.langchain_callbacks(),
            "metadata": {"conversation_id": conversation_id},
            "recursion_limit": 60,
        }
        inputs = {"messages": [{"role": "user", "content": request.message}]}
        async for mode, payload in agent.astream(inputs, config=config, stream_mode=["messages", "updates"]):
            if mode == "messages":
                chunk, meta = payload
                # Only the orchestrator's own model output; sub-agents (spawned from a tool) stay out.
                top_level = "tools:" not in meta.get("checkpoint_ns", "")
                if isinstance(chunk, AIMessageChunk) and meta.get("langgraph_node") == "model" and top_level:
                    text = _text(chunk.content)
                    if text:
                        queue.put_nowait(("token", {"text": text}))
                continue
            for update in (payload or {}).values():
                if not isinstance(update, dict):
                    continue
                if "todos" in update:
                    queue.put_nowait(("todos", {"todos": update["todos"]}))
                for msg in update.get("messages", []) or []:
                    if isinstance(msg, AIMessage):
                        for call in msg.tool_calls:
                            queue.put_nowait(("tool", {"id": call["id"], "name": call["name"], "args": call["args"], "via": _via(call["name"])}))
                    elif isinstance(msg, ToolMessage):
                        queue.put_nowait(("result", {"id": msg.tool_call_id, "name": msg.name, "status": msg.status, "preview": _text(msg.content)[:400]}))
    except Exception as exc:  # surfaced in the UI
        log.exception("Turn failed")
        queue.put_nowait(("error", _describe_error(exc)))
    finally:
        activity.reset_sink(sink_token)
        queue.put_nowait(("done", {"conversation_id": conversation_id}))


@app.post("/api/chat")
async def chat(request: ChatRequest):
    conversation_id = request.conversation_id or str(uuid.uuid4())
    queue: asyncio.Queue = asyncio.Queue()

    async def stream():
        yield _sse("start", {"conversation_id": conversation_id})
        task = asyncio.create_task(_run_turn(request, conversation_id, queue))
        try:
            while True:
                event, data = await queue.get()
                yield _sse(event, data)
                if event == "done":
                    break
        finally:
            if not task.done():
                task.cancel()

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@app.get("/api/info")
async def info():
    return {
        "agent": settings.agent_id,
        "model": settings.model,
        "gateway": settings.gateway_url,
        "tools": agent_module.TOOL_NAMES,
        "routes": {
            "model": settings.model_base_url,
            "mcp": os.environ.get("MCP_URL", ""),
            "a2a": os.environ.get("SPECIALIST_A2A_URL", ""),
        },
    }


class KnobUpdate(BaseModel):
    value: str


@app.get("/api/knobs")
async def get_knobs():
    if not knobs.enabled():
        return {"enabled": False, "knobs": []}
    try:
        return {"enabled": True, "knobs": await knobs.read_all()}
    except Exception as exc:
        log.exception("Reading the policy knobs failed")
        raise HTTPException(502, f"Could not read the APIM named values: {exc}") from exc


@app.put("/api/knobs/{name}")
async def set_knob(name: str, update: KnobUpdate):
    if not knobs.enabled():
        raise HTTPException(404, "Policy knobs are not configured (APIM_RESOURCE_ID is not set)")
    try:
        value = await knobs.write(name, update.value)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.exception("Changing %s failed", name)
        raise HTTPException(502, f"Could not change {name}: {exc}") from exc
    log.info("Policy knob %s set to %s", name, value)
    return {"name": name, "value": value}


@app.get("/healthz", include_in_schema=False)
async def healthz():
    return {"status": "ok"}


@app.get("/", include_in_schema=False)
async def index():
    return FileResponse(STATIC / "index.html")
