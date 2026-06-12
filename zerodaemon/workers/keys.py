"""Dedicated worker SSH keypair — generated once, lives outside git.

The agent must never touch the operator's personal key. On first use we
generate an ed25519 keypair under the configured key dir (default '<db dir>/ssh')
and reuse it for every worker thereafter.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

_KEY_NAME = "zerodaemon_worker"
SSH_USERNAME = "zerodaemon"


def _paths(key_dir: str) -> tuple[Path, Path]:
    base = Path(key_dir)
    return base / _KEY_NAME, base / f"{_KEY_NAME}.pub"


def ensure_keypair(key_dir: str) -> tuple[str, str]:
    """Return (private_key_path, public_key_text), generating the pair if absent."""
    priv, pub = _paths(key_dir)
    if priv.exists() and pub.exists():
        return str(priv), pub.read_text().strip()

    priv.parent.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["ssh-keygen", "-t", "ed25519", "-N", "", "-C", SSH_USERNAME, "-f", str(priv)],
            capture_output=True, text=True, check=True, timeout=30,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(
            f"Could not generate worker SSH keypair (ssh-keygen): {exc}. "
            "Install openssh-client on the controller host."
        ) from exc

    os.chmod(priv, 0o600)
    logger.info("Generated dedicated worker SSH keypair at %s", priv)
    return str(priv), pub.read_text().strip()


def private_key_path(key_dir: str) -> str:
    priv, _ = _paths(key_dir)
    return str(priv)
