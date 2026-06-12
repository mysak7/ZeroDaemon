"""LangGraph StateGraph — the agent's brain, wired to the active model and SQLite memory."""

from __future__ import annotations

import logging
from typing import Literal

from langchain_core.messages import SystemMessage
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import ToolNode

from zerodaemon.agent.state import AgentState
from zerodaemon.agent.tools import get_tools
from zerodaemon.models.registry import ModelRegistry
from zerodaemon.models import providers
from zerodaemon.core.config import get_settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are ZeroDaemon, an autonomous DevSecOps AI assistant.
Your mission: monitor IP addresses, detect configuration drift, and identify security threats.

INFRASTRUCTURE:
Scans (nmap) run on a remote Kali worker VM in the cloud, NOT on this host. Reach it
via scan_services as usual. If scan_services returns needs_worker=true, call
manage_infrastructure to build a worker (a small spot Kali VM), then retry the scan.
Use manage_infrastructure to build, list, or destroy workers. Be frugal — one shared
small worker is the norm; do not spin up extra or large machines.

When a user asks you to scan a target interactively (i.e. via chat, not an automated daemon run):
- BEFORE calling scan_services, present the available scan depths with their time estimates:
    1. top-10   — ~5–15 s    (quick sanity check, 10 common ports)
    2. top-100  — ~15–45 s   (balanced, default)
    3. top-1000 — ~1–3 min   (thorough)
    4. full     — ~10–30 min (all 65535 ports, slow)
- Ask the user which depth they want, then call scan_services with their chosen preset.
- If the user already specified a depth or port range in their request, skip the prompt and use it directly.

When given an IP to analyse (automated or after depth is confirmed):
1. Check the historical scan database (query_historical_scans) to see what was open previously.
2. Search the knowledge base for any relevant past findings or threat intel on this target.
3. Run a live service scan to discover current open ports and versions.
4. Compare results — flag any new ports or changed service versions.
5. Search for recent CVEs or exploit activity related to discovered services.
6. Report findings clearly: what changed, what's risky, what to do next.

MEMORY & FRESHNESS:
query_historical_scans (SQLite) is the source of truth for a target's CURRENT state.
search_knowledge_base (RAG) is for context and cross-history recall; its results carry
an age_days field and threat intel may be flagged stale — weigh old findings accordingly
and re-verify anything time-sensitive rather than treating it as current fact.

Be concise, technical, and actionable. Think like a senior security engineer.
"""


def _should_continue(state: AgentState) -> Literal["tools", "__end__"]:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return "__end__"


def build_agent_node(llm_with_tools):
    async def agent_node(state: AgentState) -> dict:
        messages = [SystemMessage(content=_SYSTEM_PROMPT)] + state["messages"]
        response = await llm_with_tools.ainvoke(messages)
        return {"messages": [response]}
    return agent_node


def build_graph(registry: ModelRegistry, checkpointer, extra_tools: list | None = None) -> tuple:
    """
    Build and compile the LangGraph agent graph.

    The active model is resolved at call time — switching via POST /models/{id}/activate
    takes effect on the very next invocation with no restart required.

    checkpointer is provided by the caller (AsyncSqliteSaver backed by the app DB)
    so that conversation history survives server restarts.

    extra_tools: optional additional tools (e.g. from MCP) to include alongside
    the built-in tools.
    """
    settings = get_settings()

    active = registry.get_active()
    llm = providers.build_llm(active, settings)

    # The builder sub-agent is exposed as one tool alongside the scan tools.
    from zerodaemon.agent.builder import build_infra_tool
    infra_tool = build_infra_tool(llm)
    tools = get_tools((extra_tools or []) + [infra_tool])

    llm_with_tools = llm.bind_tools(tools)

    logger.info("Building agent graph with model: %s (%s)", active.id, active.provider)

    builder = StateGraph(AgentState)
    builder.add_node("agent", build_agent_node(llm_with_tools))
    builder.add_node("tools", ToolNode(tools))

    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", _should_continue, {"tools": "tools", "__end__": END})
    builder.add_edge("tools", "agent")

    return builder.compile(checkpointer=checkpointer), active.id
