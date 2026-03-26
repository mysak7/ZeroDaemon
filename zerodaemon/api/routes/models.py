"""Models API — list models, switch active model, view usage."""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from zerodaemon.api.deps import get_registry, get_settings_dep
from zerodaemon.models.registry import ModelRegistry
from zerodaemon.models.schemas import ModelEntry, UsageRecord, UsageStats
from zerodaemon.core.config import Settings
from zerodaemon.models import usage as usage_module

router = APIRouter()


@router.get("", response_model=list[ModelEntry], summary="List all registered models")
def list_models(registry: ModelRegistry = Depends(get_registry)) -> list[ModelEntry]:
    """Returns all models from config/models.yaml, marking the active one."""
    return registry.get_all()


@router.get("/usage/stats", response_model=UsageStats, summary="Aggregate usage statistics")
async def usage_stats(settings: Settings = Depends(get_settings_dep)) -> UsageStats:
    """Total calls, tokens, and cost broken down by model."""
    return await usage_module.get_stats(settings.db_path)


@router.get("/usage", response_model=list[UsageRecord], summary="LLM call log")
async def usage_log(
    limit: int = Query(100, ge=1, le=1000),
    model_id: Optional[str] = Query(None),
    settings: Settings = Depends(get_settings_dep),
) -> list[UsageRecord]:
    """Recent LLM invocations with token counts and costs."""
    return await usage_module.get_log(settings.db_path, limit=limit, model_id=model_id)


@router.get("/{model_id:path}", response_model=ModelEntry, summary="Get a specific model")
def get_model(
    model_id: str,
    registry: ModelRegistry = Depends(get_registry),
) -> ModelEntry:
    model = registry.get(model_id)
    if model is None:
        raise HTTPException(status_code=404, detail=f"Model '{model_id}' not found")
    return model


@router.post("/{model_id:path}/activate", response_model=ModelEntry, summary="Switch active model")
async def activate_model(
    model_id: str,
    registry: ModelRegistry = Depends(get_registry),
) -> ModelEntry:
    """
    Set the active model. Takes effect on the next agent invocation — no restart needed.
    Writes the change to config/models.yaml atomically.
    """
    try:
        return await registry.set_active(model_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
