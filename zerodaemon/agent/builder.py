"""Infrastructure builder — a focused LangGraph sub-agent that provisions and
tears down cloud worker VMs.

It is exposed to the main agent as a single tool, `manage_infrastructure`, so
the main ReAct loop stays about scanning while a separate, narrowly-scoped
context handles the cloud-mutating actions (and their guardrails).
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import StructuredTool
from langchain_core.tools import tool as lc_tool
from langgraph.graph import START, END, StateGraph
from langgraph.prebuilt import ToolNode

from zerodaemon.agent.state import AgentState
from zerodaemon.workers import manager
from zerodaemon.workers.base import WorkerProviderError
from zerodaemon.workers.schemas import WorkerCreate

logger = logging.getLogger(__name__)

_BUILDER_PROMPT = """\
You are ZeroDaemon's infrastructure builder. You manage ephemeral Kali worker
VMs in the cloud that the scanner uses to run nmap and other tools.

Principles:
- Be frugal. Prefer spot and the smallest allowed machine. Never request large VMs.
- There is normally ONE shared worker. Before building, list_workers — if a healthy
  worker already exists, reuse it instead of building another.
- Hard limits (max workers, allowed machine types, TTL) are enforced for you; if a
  request is rejected, explain why rather than retrying blindly.
- After acting, report concisely: what you built/destroyed, its IP, zone, spot vs
  on-demand, and the TTL.
"""


# --------------------------------------------------------------------- tools
def build_worker(machine_type: str = "", zone: str = "", spot: bool = True, label: str = "") -> str:
    """Provision a new cloud worker VM (Kali on a small spot instance by default).

    Leave machine_type/zone empty to use the configured defaults. Tries spot first
    and falls back to on-demand if spot capacity is unavailable. Returns the new
    worker's details or an error explaining which guardrail blocked it.
    """
    mgr = manager.get_manager()
    if mgr is None:
        return json.dumps({"error": "Worker manager not initialised"})
    try:
        rec = mgr.create_worker(WorkerCreate(
            machine_type=machine_type or None,
            zone=zone or None,
            spot=spot,
            label=label or None,
        ))
        return rec.model_dump_json()
    except WorkerProviderError as exc:
        return json.dumps({"error": str(exc)})
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"error": f"unexpected: {exc}"})


def destroy_worker(worker_id: str) -> str:
    """Delete a worker VM by its id. Idempotent."""
    mgr = manager.get_manager()
    if mgr is None:
        return json.dumps({"error": "Worker manager not initialised"})
    try:
        rec = mgr.destroy_worker(worker_id)
        return rec.model_dump_json()
    except WorkerProviderError as exc:
        return json.dumps({"error": str(exc)})


def list_workers() -> str:
    """List current worker VMs (id, status, IP, zone, machine type, spot/on-demand)."""
    mgr = manager.get_manager()
    if mgr is None:
        return json.dumps({"error": "Worker manager not initialised"})
    return json.dumps([w.model_dump() for w in mgr.list_workers()], default=str)


def _builder_tools() -> list:
    return [lc_tool(build_worker), lc_tool(destroy_worker), lc_tool(list_workers)]


# --------------------------------------------------------------------- subgraph
def build_builder_subgraph(llm):
    """Compile a small ReAct graph wired to the provisioning tools."""
    tools = _builder_tools()
    llm_with_tools = llm.bind_tools(tools)

    async def agent_node(state: AgentState) -> dict:
        messages = [SystemMessage(content=_BUILDER_PROMPT)] + state["messages"]
        return {"messages": [await llm_with_tools.ainvoke(messages)]}

    def should_continue(state: AgentState):
        last = state["messages"][-1]
        return "tools" if getattr(last, "tool_calls", None) else END

    builder = StateGraph(AgentState)
    builder.add_node("agent", agent_node)
    builder.add_node("tools", ToolNode(tools))
    builder.add_edge(START, "agent")
    builder.add_conditional_edges("agent", should_continue, {"tools": "tools", END: END})
    builder.add_edge("tools", "agent")
    return builder.compile()


def build_infra_tool(llm) -> StructuredTool:
    """Wrap the builder subgraph as one tool for the main agent."""
    subgraph = build_builder_subgraph(llm)

    async def manage_infrastructure(instruction: str) -> str:
        """Build, list, or destroy cloud worker VMs. Pass a natural-language
        instruction, e.g. 'build a worker', 'destroy worker <id>', or
        'how many workers are running?'. Use this when a scan reports
        needs_worker, or when the user asks to manage infrastructure."""
        result = await subgraph.ainvoke(
            {"messages": [HumanMessage(content=instruction)], "thread_id": "builder", "active_model_id": ""}
        )
        last = result["messages"][-1]
        return last.content if hasattr(last, "content") else str(last)

    return StructuredTool.from_function(
        coroutine=manage_infrastructure,
        name="manage_infrastructure",
        description=manage_infrastructure.__doc__,
    )
