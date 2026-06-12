"""SSH exec layer — run commands inside the Kali container on the active worker.

Scan tools (nmap today; nikto/nuclei later) build a command string and hand it
here. There is deliberately no local fallback: if no worker is running the
caller gets NoActiveWorkerError and can decide to build one.
"""

from __future__ import annotations

import logging
import subprocess
from dataclasses import dataclass

from zerodaemon.workers import keys, manager
from zerodaemon.core.config import get_settings

logger = logging.getLogger(__name__)


class NoActiveWorkerError(RuntimeError):
    """No running worker to dispatch a scan to."""


@dataclass
class RemoteResult:
    returncode: int
    stdout: str
    stderr: str


def _ssh_base(private_key: str, host: str) -> list[str]:
    return [
        "ssh",
        "-i", private_key,
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "ConnectTimeout=15",
        "-o", "BatchMode=yes",
        f"{keys.SSH_USERNAME}@{host}",
    ]


def active_worker_ip() -> str:
    """Public IP of the active worker, or raise NoActiveWorkerError."""
    mgr = manager.get_manager()
    worker = mgr.get_active_worker() if mgr else None
    if worker is None or not worker.public_ip:
        raise NoActiveWorkerError(
            "No active cloud worker. Build one with build_worker / the "
            "manage_infrastructure tool before running remote scans."
        )
    return worker.public_ip


def run_in_kali(command: str, timeout: int = 600) -> RemoteResult:
    """Run a shell command inside the Kali container on the active worker."""
    host = active_worker_ip()
    settings = get_settings()
    private_key = keys.private_key_path(settings.resolved_ssh_key_dir())

    # Run inside the container; bash -lc so PATH and shell builtins behave.
    remote_cmd = f"sudo docker exec kali bash -lc {_shquote(command)}"
    cmd = _ssh_base(private_key, host) + [remote_cmd]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        raise NoActiveWorkerError(f"Remote command timed out after {timeout}s") from exc
    return RemoteResult(proc.returncode, proc.stdout, proc.stderr)


def _shquote(s: str) -> str:
    """Single-quote a string for safe embedding in a remote shell command."""
    return "'" + s.replace("'", "'\\''") + "'"
