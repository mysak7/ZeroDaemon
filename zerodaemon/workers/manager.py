"""WorkerManager — the one place that owns worker lifecycle.

Responsibilities:
  * load config/workers.yaml and build the active provider
  * enforce hard guardrails (max workers, allowed machine types, TTL) in CODE,
    so the LLM cannot exceed them no matter the prompt
  * persist workers to SQLite so they survive controller restarts
  * reconcile the DB against what actually exists in the cloud

All methods are synchronous (subprocess + sqlite3). Async callers (REST,
agent tools running in threads) offload via asyncio.to_thread where needed.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

import yaml

from zerodaemon.workers import keys
from zerodaemon.workers.base import ProvisionSpec, WorkerProviderError
from zerodaemon.workers.providers import build_provider, known_providers
from zerodaemon.workers.schemas import ProviderInfo, WorkerCreate, WorkerRecord

logger = logging.getLogger(__name__)

_WORKER_COLS = (
    "id, provider, instance_name, zone, region, machine_type, public_ip, "
    "provisioning_model, status, created_ts, ttl_seconds, expires_ts, "
    "is_active, label, error"
)


class WorkerManager:
    def __init__(self, config_path: str, db_path: str, key_dir: str) -> None:
        self._config_path = Path(config_path)
        self._db_path = db_path
        self._key_dir = key_dir
        self._config: dict = {}
        self._load_config()

    # ----------------------------------------------------------------- config
    def _load_config(self) -> None:
        if self._config_path.exists():
            with open(self._config_path) as f:
                self._config = yaml.safe_load(f) or {}
        else:
            self._config = {}

    @property
    def _limits(self) -> dict:
        return self._config.get("limits", {}) or {}

    def _provider_config(self, name: str) -> dict:
        for p in self._config.get("providers", []):
            if p.get("name") == name:
                return p
        return {}

    def _active_provider_name(self, override: Optional[str] = None) -> str:
        return override or self._config.get("active_provider", "gcp")

    def _build_provider(self, name: str):
        cfg = self._provider_config(name)
        provider = build_provider(name, cfg)
        if provider is None:
            raise WorkerProviderError(f"Unknown worker provider '{name}'")
        return provider

    # --------------------------------------------------------------------- db
    def _conn(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _row_to_record(self, row: sqlite3.Row) -> WorkerRecord:
        d = dict(row)
        d["is_active"] = bool(d.get("is_active"))
        return WorkerRecord(**d)

    def list_workers(self, include_terminated: bool = False) -> list[WorkerRecord]:
        conn = self._conn()
        try:
            if include_terminated:
                rows = conn.execute(
                    f"SELECT {_WORKER_COLS} FROM workers ORDER BY created_ts DESC"
                ).fetchall()
            else:
                rows = conn.execute(
                    f"SELECT {_WORKER_COLS} FROM workers "
                    "WHERE status NOT IN ('terminated') ORDER BY created_ts DESC"
                ).fetchall()
            return [self._row_to_record(r) for r in rows]
        finally:
            conn.close()

    def get_worker(self, worker_id: str) -> Optional[WorkerRecord]:
        conn = self._conn()
        try:
            row = conn.execute(
                f"SELECT {_WORKER_COLS} FROM workers WHERE id=?", (worker_id,)
            ).fetchone()
            return self._row_to_record(row) if row else None
        finally:
            conn.close()

    def get_active_worker(self) -> Optional[WorkerRecord]:
        """The running worker scans should be dispatched to, if any."""
        conn = self._conn()
        try:
            row = conn.execute(
                f"SELECT {_WORKER_COLS} FROM workers "
                "WHERE is_active=1 AND status='running' ORDER BY created_ts DESC LIMIT 1"
            ).fetchone()
            return self._row_to_record(row) if row else None
        finally:
            conn.close()

    def _live_count(self) -> int:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT COUNT(*) AS n FROM workers WHERE status IN ('provisioning','running')"
            ).fetchone()
            return int(row["n"])
        finally:
            conn.close()

    def _insert(self, rec: WorkerRecord) -> None:
        conn = self._conn()
        try:
            conn.execute(
                f"INSERT INTO workers ({_WORKER_COLS}) VALUES "
                "(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    rec.id, rec.provider, rec.instance_name, rec.zone, rec.region,
                    rec.machine_type, rec.public_ip, rec.provisioning_model, rec.status,
                    rec.created_ts, rec.ttl_seconds, rec.expires_ts,
                    int(rec.is_active), rec.label, rec.error,
                ),
            )
            conn.commit()
        finally:
            conn.close()

    def _update(self, worker_id: str, **fields) -> None:
        if not fields:
            return
        if "is_active" in fields:
            fields["is_active"] = int(fields["is_active"])
        cols = ", ".join(f"{k}=?" for k in fields)
        conn = self._conn()
        try:
            conn.execute(
                f"UPDATE workers SET {cols} WHERE id=?",
                (*fields.values(), worker_id),
            )
            conn.commit()
        finally:
            conn.close()

    def _deactivate_all(self) -> None:
        conn = self._conn()
        try:
            conn.execute("UPDATE workers SET is_active=0")
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------- guardrails
    def _check_machine_type(self, machine_type: str) -> None:
        allowed = self._limits.get("allowed_machine_types")
        if allowed and machine_type not in allowed:
            raise WorkerProviderError(
                f"Machine type '{machine_type}' is not allowed. "
                f"Permitted: {', '.join(allowed)}. (Guardrail: no large VMs.)"
            )

    def _check_capacity(self) -> None:
        max_workers = int(self._limits.get("max_workers", 2))
        if self._live_count() >= max_workers:
            raise WorkerProviderError(
                f"Worker limit reached ({max_workers}). Destroy an existing worker first. "
                "(Guardrail: max concurrent workers.)"
            )

    def _ssh_source_cidr(self) -> str:
        configured = (self._config.get("ssh_source_cidr") or "").strip()
        if configured:
            return configured
        # Auto-detect the controller's public IP and lock SSH to that /32.
        try:
            import httpx
            ip = httpx.get("https://api.ipify.org", timeout=10).text.strip()
            if ip:
                return f"{ip}/32"
        except Exception as exc:
            logger.warning("Could not auto-detect public IP (%s) — opening SSH to 0.0.0.0/0", exc)
        return "0.0.0.0/0"

    # --------------------------------------------------------------- lifecycle
    def create_worker(self, req: WorkerCreate) -> WorkerRecord:
        provider_name = self._active_provider_name(req.provider)
        provider = self._build_provider(provider_name)
        if not provider.is_available():
            raise WorkerProviderError(
                f"Provider '{provider_name}' is not usable on this host "
                "(CLI missing or not authenticated). Run e.g. `gcloud auth login`."
            )

        pcfg = self._provider_config(provider_name)
        machine_type = req.machine_type or pcfg.get("machine_type") or provider.default_machine_type()
        if not machine_type:
            raise WorkerProviderError("No machine type configured for this provider")
        self._check_machine_type(machine_type)
        self._check_capacity()

        ttl = int(req.ttl_seconds or self._limits.get("ttl_seconds", 86400))
        zones = [req.zone] if req.zone else (pcfg.get("zones") or provider.describe_zones())
        if not zones:
            raise WorkerProviderError("No zones configured for this provider")
        prefer_spot = pcfg.get("prefer_spot", True) if req.spot is None else req.spot

        _, public_key = keys.ensure_keypair(self._key_dir)

        spec = ProvisionSpec(
            machine_type=machine_type,
            zones=zones,
            ttl_seconds=ttl,
            ssh_username=keys.SSH_USERNAME,
            ssh_public_key=public_key,
            ssh_source_cidr=self._ssh_source_cidr(),
            prefer_spot=prefer_spot,
            label=req.label,
        )

        worker_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)
        expires = (now + timedelta(seconds=ttl)).isoformat()

        logger.info("Provisioning %s worker (%s, spot=%s) ...", provider_name, machine_type, prefer_spot)
        try:
            result = provider.create(spec)
        except WorkerProviderError:
            raise
        except Exception as exc:
            raise WorkerProviderError(f"Worker provisioning failed: {exc}") from exc

        rec = WorkerRecord(
            id=worker_id,
            provider=provider_name,
            instance_name=result.instance_name,
            zone=result.zone,
            region=result.region,
            machine_type=result.machine_type,
            public_ip=result.public_ip,
            provisioning_model=result.provisioning_model,
            status="running",
            created_ts=now.isoformat(),
            ttl_seconds=ttl,
            expires_ts=expires,
            is_active=True,
            label=req.label,
        )
        # One shared active worker — this becomes the active one.
        self._deactivate_all()
        self._insert(rec)
        logger.info("Worker %s provisioned: %s @ %s (%s)",
                    worker_id, result.public_ip, result.zone, result.provisioning_model)
        return rec

    def destroy_worker(self, worker_id: str) -> WorkerRecord:
        rec = self.get_worker(worker_id)
        if rec is None:
            raise WorkerProviderError(f"Worker '{worker_id}' not found")
        provider = self._build_provider(rec.provider)
        self._update(worker_id, status="destroying")
        try:
            provider.destroy(rec.instance_name, rec.zone)
        except Exception as exc:
            self._update(worker_id, status="error", error=str(exc))
            raise WorkerProviderError(f"Failed to destroy worker: {exc}") from exc
        self._update(worker_id, status="terminated", is_active=False, public_ip=None)
        logger.info("Worker %s (%s) destroyed", worker_id, rec.instance_name)
        return self.get_worker(worker_id)

    def reconcile(self) -> dict:
        """Mark DB workers terminated if they no longer exist in the cloud."""
        provider_name = self._active_provider_name()
        try:
            provider = self._build_provider(provider_name)
            if not provider.is_available():
                return {"reconciled": 0, "note": f"{provider_name} not available"}
            live = {w["instance_name"] for w in provider.list_managed()}
        except Exception as exc:
            return {"reconciled": 0, "error": str(exc)}

        terminated = 0
        for rec in self.list_workers():
            if rec.provider == provider_name and rec.instance_name not in live \
                    and rec.status in ("running", "provisioning", "destroying"):
                self._update(rec.id, status="terminated", is_active=False, public_ip=None)
                terminated += 1
        return {"reconciled": terminated, "cloud_instances": len(live)}

    # ----------------------------------------------------------------- providers
    def provider_infos(self) -> list[ProviderInfo]:
        infos: list[ProviderInfo] = []
        configured = {p.get("name") for p in self._config.get("providers", [])}
        for name in sorted(set(known_providers()) | configured):
            pcfg = self._provider_config(name)
            try:
                provider = self._build_provider(name)
                available = provider.is_available()
                zones = provider.describe_zones()
                mt = provider.default_machine_type()
            except Exception:
                available, zones, mt = False, [], None
            infos.append(ProviderInfo(
                name=name,
                enabled=bool(pcfg.get("enabled", name in known_providers())),
                available=available,
                machine_type=mt,
                zones=zones,
                detail="" if name in known_providers() else "not implemented",
            ))
        return infos


# --------------------------------------------------------------------- singleton
_manager: Optional[WorkerManager] = None


def init_manager(config_path: str, db_path: str, key_dir: str) -> WorkerManager:
    global _manager
    _manager = WorkerManager(config_path, db_path, key_dir)
    return _manager


def get_manager() -> Optional[WorkerManager]:
    return _manager
