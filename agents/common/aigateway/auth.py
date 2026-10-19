"""Managed identity -> bearer token for APIM.

In Container Apps ``AZURE_CLIENT_ID`` selects the agent's user-assigned identity. Locally,
DefaultAzureCredential falls back to the developer's Azure CLI login (which APIM will reject,
by design, unless that identity is added to the allow-list).
"""

from collections.abc import Generator
from functools import lru_cache

import httpx
from azure.identity import DefaultAzureCredential, get_bearer_token_provider

from aigateway.config import settings


@lru_cache
def credential() -> DefaultAzureCredential:
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


@lru_cache
def token_provider():
    """Callable returning a (cached, auto-refreshed) access token string."""
    return get_bearer_token_provider(credential(), settings.token_scope)


class GatewayAuth(httpx.Auth):
    """Adds the agent's managed identity token to every request (MCP and A2A clients)."""

    def auth_flow(self, request: httpx.Request) -> Generator[httpx.Request, httpx.Response, None]:
        request.headers["Authorization"] = f"Bearer {token_provider()()}"
        yield request
