"""GCP worker provider — builds Kali-capable VMs via the `gcloud` CLI.

Auth is whatever `gcloud` is logged into locally (gcloud auth login). The VM
boots plain Debian 12 and a startup-script runs a kalilinux/kali-rolling
container with the scan tools; the controller execs into it over SSH.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Optional

from zerodaemon.workers.base import (
    ProvisionResult,
    ProvisionSpec,
    WorkerProvider,
    WorkerProviderError,
)

logger = logging.getLogger(__name__)

_FIREWALL_RULE = "zerodaemon-ssh"
_NETWORK_TAG = "zerodaemon-worker"
_LABEL = "managed-by=zerodaemon"

# Substrings in gcloud stderr that mean "this zone/spot has no capacity right
# now" — safe to retry elsewhere rather than aborting.
_STOCKOUT_HINTS = (
    "ZONE_RESOURCE_POOL_EXHAUSTED",
    "does not have enough resources",
    "resource pool exhausted",
    "QUOTA_EXCEEDED",
    "no available zones",
)


def _startup_script(kali_image: str, ssh_username: str) -> str:
    return f"""#!/bin/bash
set -e
export DEBIAN_FRONTEND=noninteractive
# Let the SSH user reach docker without an interactive password.
echo '{ssh_username} ALL=(ALL) NOPASSWD: ALL' > /etc/sudoers.d/zerodaemon
chmod 440 /etc/sudoers.d/zerodaemon
apt-get update -y
apt-get install -y docker.io
systemctl enable --now docker
docker pull {kali_image}
docker rm -f kali 2>/dev/null || true
docker run -d --name kali --network host --restart unless-stopped {kali_image} sleep infinity
docker exec kali bash -c "apt-get update -y && apt-get install -y nmap whois dnsutils curl"
touch /var/lib/zerodaemon-ready
"""


class GcpProvider(WorkerProvider):
    name = "gcp"

    def __init__(self, config: dict) -> None:
        self.config = config or {}
        self._project: Optional[str] = self.config.get("project") or None

    # ------------------------------------------------------------------ utils
    def _run(self, args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
        cmd = ["gcloud", *args, "--quiet"]
        if self._project:
            cmd += ["--project", self._project]
        logger.debug("gcloud: %s", " ".join(cmd))
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)

    def _resolve_project(self) -> Optional[str]:
        if self._project:
            return self._project
        try:
            res = subprocess.run(
                ["gcloud", "config", "get-value", "project"],
                capture_output=True, text=True, timeout=15,
            )
            val = res.stdout.strip()
            if val and val != "(unset)":
                self._project = val
        except Exception:
            pass
        return self._project

    @staticmethod
    def _region_of(zone: str) -> str:
        return zone.rsplit("-", 1)[0]

    # -------------------------------------------------------------- interface
    def is_available(self) -> bool:
        if shutil.which("gcloud") is None:
            return False
        try:
            res = subprocess.run(
                ["gcloud", "auth", "list", "--filter=status:ACTIVE", "--format=value(account)"],
                capture_output=True, text=True, timeout=15,
            )
            return bool(res.stdout.strip())
        except Exception:
            return False

    def default_machine_type(self) -> Optional[str]:
        return self.config.get("machine_type")

    def describe_zones(self) -> list[str]:
        return list(self.config.get("zones", []))

    def _ensure_firewall(self, source_cidr: str) -> None:
        """Create or update the SSH firewall rule restricting access to source_cidr."""
        describe = self._run(["compute", "firewall-rules", "describe", _FIREWALL_RULE], timeout=60)
        if describe.returncode == 0:
            self._run([
                "compute", "firewall-rules", "update", _FIREWALL_RULE,
                f"--source-ranges={source_cidr}",
            ], timeout=60)
            return
        created = self._run([
            "compute", "firewall-rules", "create", _FIREWALL_RULE,
            "--direction=INGRESS", "--action=ALLOW", "--rules=tcp:22",
            f"--source-ranges={source_cidr}", f"--target-tags={_NETWORK_TAG}",
        ], timeout=90)
        if created.returncode != 0:
            # Non-fatal: default-allow-ssh may already cover it; log and continue.
            logger.warning("gcp: could not create firewall rule: %s", created.stderr.strip())

    def _create_in_zone(self, name: str, zone: str, spot: bool, spec: ProvisionSpec,
                        script_path: str) -> subprocess.CompletedProcess:
        args = [
            "compute", "instances", "create", name,
            f"--zone={zone}",
            f"--machine-type={spec.machine_type}",
            f"--image-family={self.config.get('image_family', 'debian-12')}",
            f"--image-project={self.config.get('image_project', 'debian-cloud')}",
            f"--boot-disk-size={self.config.get('boot_disk_gb', 20)}GB",
            f"--labels={_LABEL}",
            f"--tags={_NETWORK_TAG}",
            f"--metadata=ssh-keys={spec.ssh_username}:{spec.ssh_public_key}",
            f"--metadata-from-file=startup-script={script_path}",
            f"--max-run-duration={spec.ttl_seconds}s",
            "--instance-termination-action=DELETE",
            "--format=json",
        ]
        if spot:
            args.append("--provisioning-model=SPOT")
        return self._run(args, timeout=300)

    def create(self, spec: ProvisionSpec) -> ProvisionResult:
        if not self._resolve_project():
            raise WorkerProviderError(
                "No GCP project configured. Set `project` in config/workers.yaml "
                "or run `gcloud config set project <id>`."
            )
        self._ensure_firewall(spec.ssh_source_cidr)

        name = f"zerodaemon-worker-{int(time.time())}"
        script = _startup_script(
            self.config.get("kali_image", "kalilinux/kali-rolling"),
            spec.ssh_username,
        )

        with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as fh:
            fh.write(script)
            script_path = fh.name

        # Order of attempts: spot across all zones first (if preferred), then on-demand.
        modes = [True, False] if spec.prefer_spot else [False]
        errors: list[str] = []
        try:
            for spot in modes:
                for zone in spec.zones:
                    res = self._create_in_zone(name, zone, spot, spec, script_path)
                    if res.returncode == 0:
                        return self._parse_create(res.stdout, zone, spec.machine_type,
                                                  "SPOT" if spot else "STANDARD")
                    err = res.stderr.strip()
                    errors.append(f"[{'spot' if spot else 'on-demand'} {zone}] {err.splitlines()[-1] if err else 'unknown'}")
                    if not any(h in err for h in _STOCKOUT_HINTS):
                        # A real error (auth, quota of a different kind, bad config) —
                        # no point hammering other zones with the same request.
                        raise WorkerProviderError(f"gcloud create failed: {err}")
            raise WorkerProviderError(
                "No capacity for the worker in any configured zone (spot and on-demand both exhausted). "
                "Tried: " + "; ".join(errors)
            )
        finally:
            Path(script_path).unlink(missing_ok=True)

    def _parse_create(self, stdout: str, zone: str, machine_type: str,
                      provisioning_model: str) -> ProvisionResult:
        try:
            data = json.loads(stdout)
            inst = data[0] if isinstance(data, list) else data
        except (json.JSONDecodeError, IndexError, KeyError) as exc:
            raise WorkerProviderError(f"could not parse gcloud output: {exc}")

        public_ip = None
        for nic in inst.get("networkInterfaces", []):
            for ac in nic.get("accessConfigs", []):
                if ac.get("natIP"):
                    public_ip = ac["natIP"]
                    break
        return ProvisionResult(
            instance_name=inst.get("name"),
            zone=zone,
            region=self._region_of(zone),
            machine_type=machine_type,
            public_ip=public_ip,
            provisioning_model=provisioning_model,
        )

    def destroy(self, instance_name: str, zone: Optional[str]) -> None:
        args = ["compute", "instances", "delete", instance_name]
        if zone:
            args.append(f"--zone={zone}")
        res = self._run(args, timeout=180)
        if res.returncode != 0:
            err = res.stderr.strip()
            if "was not found" in err or "404" in err:
                return  # already gone — idempotent
            raise WorkerProviderError(f"gcloud delete failed: {err}")

    def list_managed(self) -> list[dict]:
        res = self._run([
            "compute", "instances", "list",
            f"--filter=labels.{_LABEL.replace('=', ':')}",
            "--format=json",
        ], timeout=60)
        if res.returncode != 0:
            raise WorkerProviderError(f"gcloud list failed: {res.stderr.strip()}")
        try:
            data = json.loads(res.stdout or "[]")
        except json.JSONDecodeError:
            return []
        out = []
        for inst in data:
            zone = (inst.get("zone") or "").rsplit("/", 1)[-1]
            ip = None
            for nic in inst.get("networkInterfaces", []):
                for ac in nic.get("accessConfigs", []):
                    ip = ac.get("natIP") or ip
            out.append({
                "instance_name": inst.get("name"),
                "zone": zone,
                "public_ip": ip,
                "status": inst.get("status"),
            })
        return out
