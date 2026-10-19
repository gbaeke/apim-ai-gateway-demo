"""Specialist: a Well-Architected reviewer built with LangChain Deep Agents.

It has one local tool (a pillar checklist) and calls the model through APIM. It has no MCP or A2A
access of its own: APIM policy only grants this identity the model API.
"""

from deepagents import create_deep_agent
from langchain_core.tools import tool

from aigateway.model import chat_model

_CHECKLISTS = {
    "reliability": [
        "Define availability and recovery targets (SLO, RTO, RPO) per user flow",
        "Remove single points of failure: zones, regions, replicas",
        "Retries with backoff, timeouts and circuit breakers on every dependency",
        "Health probes and self-healing; tested failover and backups",
    ],
    "security": [
        "Managed identities everywhere; no secrets or keys in code or config",
        "Least-privilege RBAC; separate identities per workload",
        "Private networking or IP restrictions for data-plane endpoints",
        "Central logging of authentication and authorization decisions",
    ],
    "cost": [
        "Right-size SKUs and capacity; scale to zero where possible",
        "Token budgets and quotas per consumer for AI workloads",
        "Tag resources for cost allocation; budgets and alerts",
        "Cache repeated work (semantic cache for LLM calls)",
    ],
    "operational-excellence": [
        "Infrastructure as code and repeatable deployments",
        "Distributed tracing across services (W3C trace context)",
        "Dashboards and alerts on golden signals and token usage",
        "Safe deployment practices: revisions, canaries, rollback",
    ],
    "performance": [
        "Load test against expected peak; define latency targets",
        "Autoscale on meaningful signals (requests, queue length)",
        "Stream responses for interactive AI experiences",
        "Load-balance across model deployments or regions",
    ],
}


@tool
def waf_checklist(pillar: str) -> str:
    """Return the review checklist for one Azure Well-Architected Framework pillar.

    Args:
        pillar: one of reliability, security, cost, operational-excellence, performance.
    """
    key = pillar.strip().lower().replace(" ", "-")
    items = _CHECKLISTS.get(key)
    if items is None:
        return f"Unknown pillar '{pillar}'. Choose from: {', '.join(_CHECKLISTS)}."
    return "\n".join(f"- {item}" for item in items)


SYSTEM_PROMPT = """You are the Well-Architected Reviewer, a specialist agent that other agents
consult over the A2A protocol.

You receive a description of an Azure solution (or a question about one). Review it against the
Azure Well-Architected Framework:
1. Pick the pillars that matter for the question and call `waf_checklist` for each.
2. Assess the solution against those checklist items.
3. Answer in concise Markdown: a one-line verdict, then per pillar the strengths, the risks and
   concrete recommendations (Azure services/features by name).

Stay under 350 words. Do not invent facts about the solution; state assumptions explicitly."""


def build_agent():
    return create_deep_agent(
        model=chat_model(),
        tools=[waf_checklist],
        system_prompt=SYSTEM_PROMPT,
        name="specialist-agent",
    )
