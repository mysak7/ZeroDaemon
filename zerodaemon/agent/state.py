"""LangGraph state definition for the DevSecOps agent."""

from __future__ import annotations

from typing import Annotated
from langgraph.graph.message import add_messages
from typing_extensions import TypedDict


class AgentState(TypedDict):
    messages: Annotated[list, add_messages]
    thread_id: str
    active_model_id: str   # snapshot of which model was active when this turn started
