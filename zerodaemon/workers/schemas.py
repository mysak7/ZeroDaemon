"""Pydantic schemas for the cloud worker subsystem."""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class WorkerRecord(BaseModel):
    """A provisioned (or provisioning) cloud worker VM, as stored in SQLite."""

    id: str
    provider: str
    instance_name: str
    zone: Optional[str] = None
    region: Optional[str] = None
    machine_type: Optional[str] = None
    public_ip: Optional[str] = None
    provisioning_model: Optional[str] = None  # "SPOT" | "STANDARD"
    status: str = "provisioning"              # provisioning|running|destroying|terminated|error
    created_ts: str
    ttl_seconds: Optional[int] = None
    expires_ts: Optional[str] = None
    is_active: bool = False
    label: Optional[str] = None
    error: Optional[str] = None


class WorkerCreate(BaseModel):
    """Request to build a worker. Everything optional — provider defaults apply."""

    provider: Optional[str] = None
    machine_type: Optional[str] = None
    zone: Optional[str] = None
    spot: Optional[bool] = None
    ttl_seconds: Optional[int] = None
    label: Optional[str] = None


class ProviderInfo(BaseModel):
    """Public description of a registered provider (no secrets)."""

    name: str
    enabled: bool
    available: bool                # CLI present / usable on the controller host
    machine_type: Optional[str] = None
    zones: list[str] = Field(default_factory=list)
    detail: str = ""
