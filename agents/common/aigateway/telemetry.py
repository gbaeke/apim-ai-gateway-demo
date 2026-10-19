"""OpenTelemetry: Azure Monitor export + GenAI semantic-convention spans for LangChain/LangGraph.

Call ``setup()`` before any httpx client is created so outgoing calls carry W3C ``traceparent``
headers; APIM (W3C correlation) and the downstream agent then join the same distributed trace.
Every span carries ``gen_ai.agent.id``: Foundry matches traces to the registered external agent on
that attribute, including the trace's root span (the incoming HTTP request), not just the GenAI spans.
"""

import logging
import os

from aigateway.config import settings

log = logging.getLogger(__name__)
_enabled = False


def _agent_id_processor():
    from opentelemetry.sdk.trace import SpanProcessor

    class AgentIdSpanProcessor(SpanProcessor):
        def on_start(self, span, parent_context=None) -> None:
            span.set_attribute("gen_ai.agent.id", settings.agent_id)

    return AgentIdSpanProcessor()


def setup() -> None:
    global _enabled
    connection_string = os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING")
    if not connection_string:
        log.warning("APPLICATIONINSIGHTS_CONNECTION_STRING not set; telemetry disabled")
        return

    # configure_azure_monitor instruments FastAPI itself; keep the readiness probe out of the traces
    # (with every span tagged as the agent, probes would otherwise show up as agent runs in Foundry).
    os.environ.setdefault("OTEL_PYTHON_FASTAPI_EXCLUDED_URLS", "healthz")

    from azure.monitor.opentelemetry import configure_azure_monitor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    configure_azure_monitor(
        connection_string=connection_string,
        enable_live_metrics=True,
        span_processors=[_agent_id_processor()],
    )
    # The exporter's own HTTP calls are logged at INFO by azure-core; keep the console readable.
    logging.getLogger("azure").setLevel(logging.WARNING)
    HTTPXClientInstrumentor().instrument()
    _enabled = True


def langchain_callbacks() -> list:
    """Callbacks to pass in the LangGraph run config: invoke_agent / chat / execute_tool spans."""
    if not _enabled:
        return []
    from langchain_azure_ai.callbacks.tracers import AzureAIOpenTelemetryTracer

    return [
        AzureAIOpenTelemetryTracer(
            agent_id=settings.agent_id,
            name=settings.agent_id,
            provider_name="azure.ai.openai",
            enable_content_recording=os.environ.get("AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED", "true").lower()
            == "true",
            auto_configure_azure_monitor=False,
        )
    ]
