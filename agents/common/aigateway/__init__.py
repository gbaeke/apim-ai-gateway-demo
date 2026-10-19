"""Shared plumbing for agents that reach every dependency through the APIM AI gateway.

- ``auth``: the agent's managed identity and the bearer token APIM validates.
- ``activity``: records each gateway call (status, latency, APIM headers) for the UI.
- ``model``: a LangChain chat model pointed at APIM's OpenAI v1 surface.
- ``telemetry``: OpenTelemetry export to Application Insights + the GenAI tracer.
"""

from aigateway.config import settings

__all__ = ["settings"]
