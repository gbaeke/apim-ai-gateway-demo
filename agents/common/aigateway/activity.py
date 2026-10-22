"""Gateway activity feed.

Every httpx client that talks to APIM gets these event hooks. Each call is classified (model, MCP,
A2A), timed to first byte, and annotated with the headers APIM adds (caller identity, request id,
remaining token budget). Calls are pushed to whichever sink is active in the current context, so a
chat request in the UI only sees the gateway calls it caused.
"""

import time
from collections.abc import Callable
from contextvars import ContextVar
from typing import Any

import httpx

from aigateway.auth import GatewayAuth
from aigateway.errors import response_reason

Sink = Callable[[dict[str, Any]], None]
_sink: ContextVar[Sink | None] = ContextVar("gateway_activity_sink", default=None)

_KINDS = (("/foundry/", "model"), ("/mslearn/", "mcp"), ("/specialist", "a2a"))
_APIM_HEADERS = {
    "x-apim-caller": "caller",
    "x-apim-request-id": "requestId",
    "x-ratelimit-remaining-tokens": "remainingTokens",
    "x-tokens-consumed": "tokensConsumed",
    "x-apim-blocked": "blocked",
}


def set_sink(sink: Sink | None):
    """Route activity from the current context to ``sink``; returns a token for ``reset_sink``."""
    return _sink.set(sink)


def reset_sink(token) -> None:
    _sink.reset(token)


def _kind(path: str) -> str:
    return next((kind for prefix, kind in _KINDS if prefix in path), "other")


async def _on_request(request: httpx.Request) -> None:
    request.extensions["gateway_started"] = time.perf_counter()
    if (sink := _sink.get()) is not None:
        # In flight: the UI lights up the route until the matching response event arrives.
        sink({"phase": "start", "callId": id(request), "kind": _kind(request.url.path), "method": request.method, "path": request.url.path})


async def _on_response(response: httpx.Response) -> None:
    sink = _sink.get()
    if sink is None:
        return
    request = response.request
    started = request.extensions.get("gateway_started", time.perf_counter())
    event = {
        "callId": id(request),
        "kind": _kind(request.url.path),
        "method": request.method,
        "path": request.url.path,
        "status": response.status_code,
        "ms": round((time.perf_counter() - started) * 1000),
    }
    for header, key in _APIM_HEADERS.items():
        if header in response.headers:
            event[key] = response.headers[header]
    if response.status_code >= 400:
        await response.aread()  # small error body; also lets callers read why APIM refused
        if reason := response_reason(response):
            event["reason"] = reason
    sink(event)


HOOKS = {"request": [_on_request], "response": [_on_response]}


def async_client(*, auth: httpx.Auth | None = None, timeout: float = 120.0, **kwargs: Any) -> httpx.AsyncClient:
    """An httpx client wired for the gateway: activity hooks and (optionally) managed identity auth."""
    return httpx.AsyncClient(auth=auth, timeout=timeout, event_hooks=HOOKS, follow_redirects=True, **kwargs)


def mcp_client_factory(
    headers: dict[str, str] | None = None,
    timeout: httpx.Timeout | None = None,
    auth: httpx.Auth | None = None,
) -> httpx.AsyncClient:
    """``httpx_client_factory`` for langchain-mcp-adapters' streamable HTTP transport."""
    return httpx.AsyncClient(
        headers=headers,
        timeout=timeout or httpx.Timeout(60.0, read=300.0),
        auth=auth or GatewayAuth(),
        event_hooks=HOOKS,
        follow_redirects=True,
    )
