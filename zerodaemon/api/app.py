"""FastAPI application factory with lifespan management."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from zerodaemon.core.config import get_settings
from zerodaemon.db.sqlite import init_tables
from zerodaemon.models.registry import ModelRegistry
from zerodaemon.agent import daemon
from zerodaemon.utils.deps import ensure_required
from zerodaemon.api.routes import models as models_router
from zerodaemon.api.routes import agent as agent_router
from zerodaemon.api.routes import scans as scans_router
from zerodaemon.api.routes import settings as settings_router

logger = logging.getLogger(__name__)


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

    # Start background daemon
    await daemon.start(registry)
    logger.info("Daemon loop started")

    yield

    # Shutdown
    logger.info("ZeroDaemon shutting down")
    await daemon.stop()


def create_app() -> FastAPI:
    app = FastAPI(
        title="ZeroDaemon",
        description="Local AI-driven DevSecOps assistant — monitoring, drift detection, threat intel",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(models_router.router, prefix="/models", tags=["Models"])
    app.include_router(agent_router.router, prefix="/agent", tags=["Agent"])
    app.include_router(scans_router.router, prefix="/scans", tags=["Scans"])
    app.include_router(settings_router.router, prefix="/settings", tags=["Settings"])

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
