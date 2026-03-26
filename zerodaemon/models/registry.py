"""ModelRegistry — loads config/models.yaml, manages active model, thread-safe writes."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Optional

import yaml

from zerodaemon.models.schemas import ModelEntry


class ModelRegistry:
    """Single source of truth for available LLM models and the currently active one."""

    def __init__(self, config_path: str = "config/models.yaml") -> None:
        self._path = Path(config_path)
        self._lock = asyncio.Lock()
        self._config: dict = {}
        self._models: list[ModelEntry] = []
        self._active_id: str = ""
        self._load()

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _load(self) -> None:
        """Read YAML from disk and populate in-memory state."""
        with open(self._path) as f:
            raw = yaml.safe_load(f) or {}
        self._config = raw
        self._active_id = raw.get("active", "")
        self._models = [ModelEntry(**m) for m in raw.get("models", [])]

    def _write(self) -> None:
        """Atomically write current state back to YAML using a temp file rename."""
        data = {
            "active": self._active_id,
            "models": [m.model_dump(exclude={"active"}) for m in self._models],
        }
        tmp = self._path.with_suffix(".yaml.tmp")
        with open(tmp, "w") as f:
            yaml.dump(data, f, default_flow_style=False, allow_unicode=True, sort_keys=False)
        tmp.replace(self._path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_all(self) -> list[ModelEntry]:
        """Return all registered models, marking the active one."""
        return [
            m.model_copy(update={"active": m.id == self._active_id})
            for m in self._models
        ]

    def get(self, model_id: str) -> Optional[ModelEntry]:
        """Return a single model by id, or None if not found."""
        for m in self._models:
            if m.id == model_id:
                return m.model_copy(update={"active": m.id == self._active_id})
        return None

    def get_active(self) -> ModelEntry:
        """Return the currently active model. Raises RuntimeError if misconfigured."""
        model = self.get(self._active_id)
        if model is None:
            raise RuntimeError(
                f"Active model '{self._active_id}' not found in registry. "
                "Check config/models.yaml."
            )
        return model

    async def set_active(self, model_id: str) -> ModelEntry:
        """Switch the active model. Thread-safe — writes YAML atomically."""
        async with self._lock:
            model = self.get(model_id)
            if model is None:
                raise ValueError(f"Model '{model_id}' not in registry")
            self._active_id = model_id
            self._write()
            return model.model_copy(update={"active": True})

    async def add_model(self, entry: ModelEntry) -> ModelEntry:
        """Add a new model. Raises ValueError if id already exists."""
        async with self._lock:
            if any(m.id == entry.id for m in self._models):
                raise ValueError(f"Model '{entry.id}' already exists")
            self._models.append(entry)
            self._write()
            return entry

    async def update_model(self, model_id: str, updates: dict) -> ModelEntry:
        """Update fields on an existing model. Raises ValueError if not found."""
        async with self._lock:
            for i, m in enumerate(self._models):
                if m.id == model_id:
                    updated = m.model_copy(update=updates)
                    self._models[i] = updated
                    self._write()
                    return updated.model_copy(update={"active": model_id == self._active_id})
            raise ValueError(f"Model '{model_id}' not in registry")

    async def delete_model(self, model_id: str) -> None:
        """Delete a model. Raises ValueError if active or not found."""
        async with self._lock:
            if model_id == self._active_id:
                raise ValueError(f"Cannot delete the active model '{model_id}'")
            before = len(self._models)
            self._models = [m for m in self._models if m.id != model_id]
            if len(self._models) == before:
                raise ValueError(f"Model '{model_id}' not in registry")
            self._write()

    def reload(self) -> None:
        """Re-read from disk (useful after external edits to models.yaml)."""
        self._load()
