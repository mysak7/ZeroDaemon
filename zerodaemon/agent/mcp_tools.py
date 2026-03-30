"""MCP (Model Context Protocol) tool loader — connects to a remote MCP server and
exposes its tools as LangChain-compatible callables for the agent graph."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from zerodaemon.core.config import Settings

logger = logging.getLogger(__name__)


@asynccontextmanager
async def mcp_lifespan(settings: "Settings"):
    """Async context manager that yields a list of LangChain tools from the MCP server.

    Yields an empty list if MCP is not configured or the server is unreachable,
    so the agent degrades gracefully rather than failing to start.
    """
    if not settings.mcp_server_url:
        yield []
        return

    try:
        from langchain_mcp_adapters.client import MultiServerMCPClient

        headers: dict[str, str] = {}
        if settings.mcp_api_key:
            headers["Authorization"] = f"Bearer {settings.mcp_api_key}"

        async with MultiServerMCPClient(
            {
                "mcp": {
                    "url": settings.mcp_server_url,
                    "transport": "streamable_http",
                    "headers": headers,
                }
            }
        ) as client:
            tools = client.get_tools()
            logger.info(
                "MCP: loaded %d tool(s) from %s — %s",
                len(tools),
                settings.mcp_server_url,
                [t.name for t in tools],
            )
            yield tools

    except Exception as exc:
        logger.warning(
            "MCP init failed (%s) — agent will run without MCP tools", exc
        )
        yield []
