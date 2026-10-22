"""Policy knobs: the APIM named values the gateway policies read, changed live from the UI.

Changes go through Azure Resource Manager with a dedicated identity (``GATEWAY_ADMIN_CLIENT_ID``)
that may only read and write named values. The agent's own identity has no management rights,
and the model has no tool that reaches this module. Only the knobs listed here can be read or set.
"""

import asyncio
import os
import re
from dataclasses import asdict, dataclass

import httpx
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

APIM_RESOURCE_ID = os.environ.get("APIM_RESOURCE_ID", "")
ADMIN_CLIENT_ID = os.environ.get("GATEWAY_ADMIN_CLIENT_ID", "")
ARM = "https://management.azure.com"
API_VERSION = "2024-06-01-preview"
AGENTS = ["orchestrator-agent", "specialist-agent"]
MCP_TOOLS = ["microsoft_docs_search", "microsoft_docs_fetch", "microsoft_code_sample_search"]


@dataclass(frozen=True)
class Knob:
    name: str
    group: str
    label: str
    kind: str  # "number" or "list"
    default: str  # keep in sync with infra/modules/apim.bicep
    presets: tuple[str, ...]
    options: tuple[str, ...] = ()  # allowed list items; "none" means an empty list


KNOBS = [
    Knob("orchestrator-tpm", "Model", "Orchestrator tokens per minute", "number", "50000", ("50000", "5000", "300")),
    Knob("specialist-tpm", "Model", "Specialist tokens per minute", "number", "20000", ("20000", "2000", "300")),
    Knob("mcp-allowed-callers", "MCP", "Agents allowed on Microsoft Learn MCP", "list", "orchestrator-agent", (), tuple(AGENTS)),
    Knob("mcp-blocked-tools", "MCP", "Blocked MCP tools", "list", "none", (), tuple(MCP_TOOLS)),
    Knob("mcp-calls-per-minute", "MCP", "MCP requests per minute (~5 per tool call)", "number", "300", ("300", "20", "2")),
    Knob("a2a-allowed-callers", "A2A", "Agents allowed to delegate to the specialist", "list", "orchestrator-agent", (), tuple(AGENTS)),
    Knob("a2a-calls-per-minute", "A2A", "A2A requests per minute", "number", "20", ("20", "1")),
]
_BY_NAME = {k.name: k for k in KNOBS}


def enabled() -> bool:
    return bool(APIM_RESOURCE_ID)


def _credential():
    # In Container Apps: the gateway admin identity. Locally: the developer's own Azure login.
    return ManagedIdentityCredential(client_id=ADMIN_CLIENT_ID) if ADMIN_CLIENT_ID else DefaultAzureCredential()


_cred = None


async def _headers() -> dict[str, str]:
    global _cred
    _cred = _cred or _credential()
    token = await asyncio.to_thread(_cred.get_token, f"{ARM}/.default")
    return {"Authorization": f"Bearer {token.token}"}


def validate(name: str, value: str) -> str:
    """The normalised value, or ValueError when this is not a knob or not a sensible value for it."""
    knob = _BY_NAME.get(name)
    if knob is None:
        raise ValueError(f"Unknown knob: {name}")
    value = value.strip()
    if knob.kind == "number":
        if not re.fullmatch(r"\d{1,7}", value) or int(value) < 1:
            raise ValueError(f"{name} must be a whole number between 1 and 9999999")
        return str(int(value))
    items = [item.strip() for item in value.split(",") if item.strip() and item.strip() != "none"]
    unknown = [item for item in items if item not in knob.options]
    if unknown:
        raise ValueError(f"{name} accepts {', '.join(knob.options)} (or none), not {', '.join(unknown)}")
    return ",".join(dict.fromkeys(items)) or "none"


async def read_all() -> list[dict]:
    async with httpx.AsyncClient(timeout=30) as http:
        response = await http.get(f"{ARM}{APIM_RESOURCE_ID}/namedValues", params={"api-version": API_VERSION}, headers=await _headers())
        response.raise_for_status()
    values = {nv["name"]: nv["properties"].get("value") for nv in response.json()["value"]}
    return [{**asdict(knob), "value": values.get(knob.name)} for knob in KNOBS]


async def write(name: str, value: str) -> str:
    """Set a knob and wait until APIM has applied it (named value updates are asynchronous in ARM)."""
    value = validate(name, value)
    async with httpx.AsyncClient(timeout=30) as http:
        headers = await _headers()
        response = await http.patch(
            f"{ARM}{APIM_RESOURCE_ID}/namedValues/{name}",
            params={"api-version": API_VERSION},
            headers={**headers, "If-Match": "*"},
            json={"properties": {"value": value}},
        )
        response.raise_for_status()
        poll = response.headers.get("Location")  # 202 while running, 200 once applied
        for _ in range(30):
            if response.status_code != 202 or not poll:
                break
            await asyncio.sleep(float(response.headers.get("Retry-After", "2")))
            response = await http.get(poll, headers=headers)
            response.raise_for_status()
    return value
