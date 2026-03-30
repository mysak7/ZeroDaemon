"""FastAPI dependency injection helpers."""

from __future__ import annotations

from fastapi import Request

from zerodaemon.models.registry import ModelRegistry
from zerodaemon.core.config import Settings, get_settings


def get_registry(request: Request) -> ModelRegistry:
    return request.app.state.registry


def get_settings_dep() -> Settings:
    return get_settings()


def get_graph(request: Request, registry: ModelRegistry = None):
    """
    Return (graph, model_id) from app.state.
    If the active model has changed since the graph was last compiled, rebuild it
    against the same persistent checkpointer so no conversation history is lost.
    """
    if registry is None:
        registry = get_registry(request)
    active = registry.get_active()
    if getattr(request.app.state, "graph_model_id", None) != active.id:
        from zerodaemon.agent.graph import build_graph
        mcp_tools = getattr(request.app.state, "mcp_tools", None)
        graph, model_id = build_graph(registry, request.app.state.checkpointer, extra_tools=mcp_tools)
        request.app.state.graph = graph
        request.app.state.graph_model_id = model_id
    return request.app.state.graph, request.app.state.graph_model_id
