"""Provider abstraction for building worker VMs across clouds.

Each cloud implements WorkerProvider. The WorkerManager picks the active one
and never talks to a cloud CLI directly — so adding AWS/Azure later means
writing one new subclass and registering it, with no changes elsewhere.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Optional


class WorkerProviderError(RuntimeError):
    """Raised when a provider operation fails (CLI error, stockout, bad config)."""


@dataclass
class ProvisionSpec:
    """Everything a provider needs to build one worker."""

    machine_type: str
    zones: list[str]                       # candidate zones, tried in order
    ttl_seconds: int
    ssh_username: str
    ssh_public_key: str                    # OpenSSH public key text
    ssh_source_cidr: str                   # firewall: who may reach SSH
    prefer_spot: bool = True
    label: Optional[str] = None
    extra: dict = field(default_factory=dict)


@dataclass
class ProvisionResult:
    """What a provider returns after a successful create()."""

    instance_name: str
    zone: str
    region: Optional[str]
    machine_type: str
    public_ip: Optional[str]
    provisioning_model: str                # "SPOT" | "STANDARD"


class WorkerProvider(abc.ABC):
    """Cloud-agnostic interface for managing worker VMs."""

    name: str = "base"

    @abc.abstractmethod
    def is_available(self) -> bool:
        """True if this provider's CLI is installed and authenticated enough to use."""

    @abc.abstractmethod
    def create(self, spec: ProvisionSpec) -> ProvisionResult:
        """Build one worker VM and return its connection details."""

    @abc.abstractmethod
    def destroy(self, instance_name: str, zone: Optional[str]) -> None:
        """Delete a worker VM. Idempotent — deleting a gone VM must not raise."""

    @abc.abstractmethod
    def list_managed(self) -> list[dict]:
        """List VMs this provider manages (label-filtered), as plain dicts."""

    def describe_zones(self) -> list[str]:
        return []

    def default_machine_type(self) -> Optional[str]:
        return None
