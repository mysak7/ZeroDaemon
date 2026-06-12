"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import asyncio
import logging
import os
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from zerodaemon.core.config import get_settings
from zerodaemon.db.sqlite import init_tables
from zerodaemon.models.registry import ModelRegistry
from zerodaemon.agent import daemon
from zerodaemon.agent import rag
from zerodaemon.agent.graph import build_graph
from zerodaemon.agent.mcp_tools import mcp_lifespan
from zerodaemon.utils.deps import ensure_required
from zerodaemon.workers import manager as worker_manager
from zerodaemon.api.routes import models as models_router
from zerodaemon.api.routes import agent as agent_router
from zerodaemon.api.routes import scans as scans_router
from zerodaemon.api.routes import settings as settings_router
from zerodaemon.api.routes import workers as workers_router

logger = logging.getLogger(__name__)

_AUTH_EXEMPT_PATHS = {"/", "/health", "/docs", "/redoc", "/openapi.json"}


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    logger.info("ZeroDaemon starting up")

    # Check / auto-install system tool dependencies
    auto_install = os.environ.get("ZERODAEMON_AUTO_INSTALL_DEPS", "false").lower() == "true"
    ensure_required(auto_install=auto_install)

    # Initialise database tables
    await init_tables(settings.db_path)
    logger.info("Database ready: %s", settings.db_path)

    # Initialise model registry
    registry = ModelRegistry(config_path=settings.models_config_path)
    app.state.registry = registry
    active = registry.get_active()
    logger.info("Active model: %s (%s)", active.id, active.provider)

    # Initialise RAG knowledge base (non-fatal — degraded mode if deps missing)
    try:
        rag.init_store(settings.rag_path)
        logger.info("RAG knowledge base ready: %s", settings.rag_path)
    except Exception as exc:
        logger.warning("RAG init failed (%s) — search_knowledge_base will return empty results", exc)

    # Initialise cloud worker manager (non-fatal — workers simply unavailable if it fails)
    try:
        worker_manager.init_manager(
            settings.workers_config_path, settings.db_path, settings.resolved_ssh_key_dir()
        )
        recon = await asyncio.to_thread(worker_manager.get_manager().reconcile)
        logger.info("Worker manager ready (reconcile: %s)", recon)
    except Exception as exc:
        logger.warning("Worker manager init failed (%s) — cloud workers unavailable", exc)

    # Persistent LangGraph checkpointer — keeps all conversation threads in SQLite
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    async with mcp_lifespan(settings) as mcp_tools:
        app.state.mcp_tools = mcp_tools

        async with AsyncSqliteSaver.from_conn_string(settings.db_path) as checkpointer:
            graph, model_id = build_graph(registry, checkpointer, extra_tools=mcp_tools)
            app.state.graph = graph
            app.state.graph_model_id = model_id
            app.state.checkpointer = checkpointer
            logger.info("Agent graph compiled (model: %s, persistent memory: ON)", model_id)

            # Start background daemon with checkpointer and db_path for persistence
            await daemon.start(registry, checkpointer=checkpointer, db_path=settings.db_path)
            logger.info("Daemon loop started")

            yield

            # Shutdown
            logger.info("ZeroDaemon shutting down")
            await daemon.stop()


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title="ZeroDaemon",
        description="Local AI-driven DevSecOps assistant — monitoring, drift detection, threat intel",
        version="1.0.0",
        lifespan=lifespan,
    )

    # Optional Bearer token auth — enabled only when ZERODAEMON_API_KEY is set
    if settings.api_key:
        @app.middleware("http")
        async def api_key_middleware(request: Request, call_next):
            path = request.url.path
            if path not in _AUTH_EXEMPT_PATHS and not path.startswith("/static"):
                auth = request.headers.get("Authorization", "")
                if not auth.startswith("Bearer ") or auth[7:] != settings.api_key:
                    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            return await call_next(request)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(models_router.router, prefix="/models", tags=["Models"])
    app.include_router(agent_router.router, prefix="/agent", tags=["Agent"])
    app.include_router(scans_router.router, prefix="/scans", tags=["Scans"])
    app.include_router(settings_router.router, prefix="/settings", tags=["Settings"])
    app.include_router(workers_router.router, prefix="/workers", tags=["Workers"])

    _static = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=_static), name="static")

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(_static / "index.html")

    @app.get("/health", tags=["System"])
    def health():
        registry: ModelRegistry = app.state.registry
        active = registry.get_active()
        return {"status": "ok", "active_model": active.id, "provider": active.provider}

    return app
