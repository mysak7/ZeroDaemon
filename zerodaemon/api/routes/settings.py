"""Settings API — read and update config/settings.yaml at runtime."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from zerodaemon.core.config import get_settings

router = APIRouter()

_SETTINGS_YAML = Path("config/settings.yaml")


class SettingsOut(BaseModel):
    log_level: str
    daemon_poll_interval: int
    daemon_paused: bool
    ollama_base_url: str


class SettingsPatch(BaseModel):
    log_level: Optional[str] = None
    daemon_poll_interval: Optional[int] = None
    daemon_paused: Optional[bool] = None
    ollama_base_url: Optional[str] = None


def _read_yaml() -> dict:
    if _SETTINGS_YAML.exists():
        with open(_SETTINGS_YAML) as f:
            return yaml.safe_load(f) or {}
    return {}


def _write_yaml(data: dict) -> None:
    with open(_SETTINGS_YAML, "w") as f:
        yaml.safe_dump(data, f, default_flow_style=False, allow_unicode=True)


@router.get("", response_model=SettingsOut, summary="Get application settings")
def get_current() -> SettingsOut:
    """Returns editable settings from config/settings.yaml."""
    raw = _read_yaml()
    s = get_settings()
    return SettingsOut(
        log_level=raw.get("log_level", s.log_level),
        daemon_poll_interval=raw.get("daemon_poll_interval", s.daemon_poll_interval),
        daemon_paused=raw.get("daemon_paused", s.daemon_paused),
        ollama_base_url=raw.get("ollama_base_url", s.ollama_base_url),
    )


@router.patch("", response_model=SettingsOut, summary="Update application settings")
def update_settings(body: SettingsPatch) -> SettingsOut:
    """Write changed fields to config/settings.yaml and clear the settings cache."""
    updates = body.model_dump(exclude_none=True)
    if not updates:
        raise HTTPException(status_code=400, detail="No fields provided")

    raw = _read_yaml()
    raw.update(updates)
    _write_yaml(raw)
    get_settings.cache_clear()

    return SettingsOut(
        log_level=raw.get("log_level", "INFO"),
        daemon_poll_interval=raw.get("daemon_poll_interval", 86400),
        daemon_paused=raw.get("daemon_paused", False),
        ollama_base_url=raw.get("ollama_base_url", "http://localhost:11434"),
    )
