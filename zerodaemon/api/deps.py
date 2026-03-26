"""FastAPI dependency injection helpers."""

from __future__ import annotations

from fastapi import Request

from zerodaemon.models.registry import ModelRegistry
from zerodaemon.core.config import Settings, get_settings


def get_registry(request: Request) -> ModelRegistry:
    return request.app.state.registry


def get_settings_dep() -> Settings:
    return get_settings()
