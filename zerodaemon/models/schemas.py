"""Pydantic schemas for the Models section."""

from __future__ import annotations

from typing import Optional
from pydantic import BaseModel, Field


class ModelEntry(BaseModel):
    id: str
    provider: str  # "anthropic" | "ollama" | "openai"
    input_mtok: float = 0.0   # cost per million input tokens (USD)
    output_mtok: float = 0.0  # cost per million output tokens (USD)
    max_tokens: int = 4096
    note: str = ""
    active: bool = False      # computed field — True if this is the active model


class ModelCreate(BaseModel):
    id: str
    provider: str
    input_mtok: float = 0.0
    output_mtok: float = 0.0
    max_tokens: int = 4096
    note: str = ""


class ModelUpdate(BaseModel):
    provider: Optional[str] = None
    input_mtok: Optional[float] = None
    output_mtok: Optional[float] = None
    max_tokens: Optional[int] = None
    note: Optional[str] = None


class ActivateRequest(BaseModel):
    pass  # model_id comes from path param


class UsageRecord(BaseModel):
    id: str
    ts: str
    model_id: str
    provider: str
    caller: str = "unknown"
    thread_id: Optional[str] = None
    input_tokens: Optional[int] = None
    output_tokens: Optional[int] = None
    cost_usd: Optional[float] = None
    duration_ms: Optional[int] = None
    status: str = "running"
    error: Optional[str] = None


class UsageStats(BaseModel):
    total_calls: int
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    by_model: list[dict]
