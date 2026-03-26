"""Agent API — chat, stream, daemon status, and target management."""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.responses import StreamingResponse
from langchain_core.messages import HumanMessage
from pydantic import BaseModel

from zerodaemon.agent import daemon
from zerodaemon.agent.graph import build_graph
from zerodaemon.api.deps import get_registry, get_settings_dep
from zerodaemon.core.config import Settings, get_settings
from zerodaemon.models.registry import ModelRegistry
from zerodaemon.models import usage as usage_module

logger = logging.getLogger(__name__)
router = APIRouter()


# ------------------------------------------------------------------
# Request / Response schemas
# ------------------------------------------------------------------

class ChatRequest(BaseModel):
    message: str
    thread_id: str = "default"


class ChatResponse(BaseModel):
    thread_id: str
    reply: str
    model_id: str


class DaemonStatusResponse(BaseModel):
    status: str
    last_run_ts: Optional[str]
    last_target: Optional[str]
    scheduled_targets: list[str]
    error: Optional[str]
    active_model_id: str


class TargetRequest(BaseModel):
    ip: str


# ------------------------------------------------------------------
# Endpoints
# ------------------------------------------------------------------

@router.post("/chat", response_model=ChatResponse, summary="Send a message to the agent")
async def chat(
    body: ChatRequest,
    registry: ModelRegistry = Depends(get_registry),
    settings: Settings = Depends(get_settings_dep),
) -> ChatResponse:
    """
    Invoke the LangGraph agent synchronously. The agent may call tools
    (nmap, whois, DDG search) before returning the final answer.
    """
    active = registry.get_active()
    entry_id, start_time = await usage_module.record_start(
        settings.db_path, active, caller="api/chat", thread_id=body.thread_id
    )

    try:
        graph, model_id = build_graph(registry)
        config = {"configurable": {"thread_id": body.thread_id}}
        result = await graph.ainvoke(
            {
                "messages": [HumanMessage(content=body.message)],
                "thread_id": body.thread_id,
                "active_model_id": model_id,
            },
            config=config,
        )
        last_msg = result["messages"][-1]
        reply = last_msg.content if hasattr(last_msg, "content") else str(last_msg)

        # Record token usage from AIMessage if available
        usage = getattr(last_msg, "usage_metadata", None)
        in_tok = usage.get("input_tokens") if usage else None
        out_tok = usage.get("output_tokens") if usage else None
        await usage_module.record_end(settings.db_path, entry_id, start_time, active, in_tok, out_tok)

        return ChatResponse(thread_id=body.thread_id, reply=reply, model_id=model_id)

    except Exception as exc:
        await usage_module.record_end(settings.db_path, entry_id, start_time, active, error=str(exc))
        logger.exception("Agent chat error: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.websocket("/stream")
async def agent_stream(
    websocket: WebSocket,
    thread_id: str = "default",
):
    """
    WebSocket endpoint for streaming agent token output.
    Connect, send a JSON message {"message": "..."}, receive streamed tokens.
    """
    registry: ModelRegistry = websocket.app.state.registry
    settings = get_settings()
    await websocket.accept()
    try:
        data = await websocket.receive_json()
        message = data.get("message", "")
        if not message:
            await websocket.send_json({"error": "empty message"})
            await websocket.close()
            return

        graph, model_id = build_graph(registry)
        active = registry.get_active()
        config = {"configurable": {"thread_id": thread_id}}

        entry_id, start_time = await usage_module.record_start(
            settings.db_path, active, caller="api/stream", thread_id=thread_id
        )

        await websocket.send_json({"event": "start", "model_id": model_id})

        total_in_tok: int = 0
        total_out_tok: int = 0

        try:
            async for event in graph.astream_events(
                {
                    "messages": [HumanMessage(content=message)],
                    "thread_id": thread_id,
                    "active_model_id": model_id,
                },
                config=config,
                version="v2",
            ):
                kind = event.get("event", "")
                if kind == "on_chat_model_stream":
                    chunk = event["data"]["chunk"]
                    if hasattr(chunk, "content") and chunk.content:
                        content = chunk.content
                        if isinstance(content, list):
                            text = "".join(
                                block.get("text", "") for block in content
                                if isinstance(block, dict) and block.get("type") == "text"
                            )
                        elif isinstance(content, str):
                            text = content
                        else:
                            text = ""
                        if text:
                            await websocket.send_json({"event": "token", "data": text})
                elif kind == "on_chat_model_end":
                    output = event["data"].get("output")
                    meta = getattr(output, "usage_metadata", None)
                    if meta:
                        total_in_tok += meta.get("input_tokens", 0)
                        total_out_tok += meta.get("output_tokens", 0)
                elif kind == "on_tool_start":
                    tool_name = event.get("name", "tool")
                    tool_input = event["data"].get("input", {})
                    await websocket.send_json({"event": "tool_start", "tool": tool_name, "input": tool_input})
                elif kind == "on_tool_end":
                    tool_name = event.get("name", "tool")
                    await websocket.send_json({"event": "tool_end", "tool": tool_name})

            await usage_module.record_end(
                settings.db_path, entry_id, start_time, active,
                total_in_tok or None, total_out_tok or None,
            )
            await websocket.send_json({"event": "done"})

        except Exception as exc:
            await usage_module.record_end(settings.db_path, entry_id, start_time, active, error=str(exc))
            raise

    except WebSocketDisconnect:
        logger.info("WebSocket client disconnected (thread_id=%s)", thread_id)
    except Exception as exc:
        logger.exception("WebSocket error: %s", exc)
        try:
            await websocket.send_json({"event": "error", "detail": str(exc)})
        except Exception:
            pass


@router.get("/status", response_model=DaemonStatusResponse, summary="Daemon and active model status")
def agent_status(registry: ModelRegistry = Depends(get_registry)) -> DaemonStatusResponse:
    state = daemon.get_state()
    active = registry.get_active()
    return DaemonStatusResponse(
        status=state.status,
        last_run_ts=state.last_run_ts,
        last_target=state.last_target,
        scheduled_targets=state.scheduled_targets,
        error=state.error,
        active_model_id=active.id,
    )


@router.post("/targets", summary="Add an IP to the daemon's monitoring list")
def add_target(body: TargetRequest) -> dict:
    daemon.add_target(body.ip)
    return {"message": f"Target {body.ip} added", "targets": daemon.get_state().scheduled_targets}


@router.delete("/targets/{ip}", summary="Remove an IP from the daemon's monitoring list")
def remove_target(ip: str) -> dict:
    daemon.remove_target(ip)
    return {"message": f"Target {ip} removed", "targets": daemon.get_state().scheduled_targets}
