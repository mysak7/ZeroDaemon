"""System dependency checker and installer for ZeroDaemon.

All external binaries the daemon may invoke (directly or via Python wrappers)
are declared in TOOLS. At startup, ensure_required() is called to log warnings
and optionally auto-install anything missing via the system package manager.
"""
from __future__ import annotations

import logging
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class ToolSpec:
    name: str
    description: str
    required: bool = True
    apt: Optional[str] = None
    brew: Optional[str] = None
    yum: Optional[str] = None  # also covers dnf
    install_note: Optional[str] = None  # fallback manual instructions


# ---------------------------------------------------------------------------
# Canonical tool list — add new tools here as capabilities expand
# ---------------------------------------------------------------------------

TOOLS: list[ToolSpec] = [
    ToolSpec(
        name="nmap",
        description="Network port/service scanner — required for scan_services",
        required=True,
        apt="nmap",
        brew="nmap",
        yum="nmap",
    ),
    ToolSpec(
        name="masscan",
        description="High-speed port scanner — optional, faster for large IP ranges",
        required=False,
        apt="masscan",
        brew="masscan",
        yum="masscan",
    ),
    ToolSpec(
        name="nuclei",
        description="CVE template scanner — optional, automated vulnerability detection",
        required=False,
        install_note="go install github.com/projectdiscovery/nuclei/v3/cmd/nuclei@latest",
    ),
    ToolSpec(
        name="nikto",
        description="Web server scanner — optional, web vulnerability detection",
        required=False,
        apt="nikto",
        brew="nikto",
    ),
    ToolSpec(
        name="whois",
        description="WHOIS lookup — optional, fallback IP/domain owner queries",
        required=False,
        apt="whois",
        brew="whois",
        yum="whois",
    ),
]

TOOLS_BY_NAME: dict[str, ToolSpec] = {t.name: t for t in TOOLS}


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def check_tool(name: str) -> bool:
    """Return True if the binary is available in PATH."""
    return shutil.which(name) is not None


@dataclass
class AuditResult:
    missing_required: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    present: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.missing_required) == 0

    def report(self) -> str:
        lines: list[str] = []
        if self.present:
            lines.append(f"  OK       : {', '.join(self.present)}")
        if self.missing_optional:
            lines.append(f"  OPTIONAL : {', '.join(self.missing_optional)} (not installed)")
        if self.missing_required:
            lines.append(f"  MISSING  : {', '.join(self.missing_required)} (REQUIRED)")
        return "\n".join(lines)


def audit() -> AuditResult:
    """Check every declared tool and return an AuditResult."""
    result = AuditResult()
    for spec in TOOLS:
        if check_tool(spec.name):
            result.present.append(spec.name)
        elif spec.required:
            result.missing_required.append(spec.name)
        else:
            result.missing_optional.append(spec.name)
    return result


# ---------------------------------------------------------------------------
# Installation
# ---------------------------------------------------------------------------

def _detect_pkg_manager() -> Optional[str]:
    for pm in ("apt-get", "apt", "dnf", "yum", "brew"):
        if shutil.which(pm):
            return pm
    return None


def _build_install_cmd(pm: str, package: str) -> list[str]:
    if pm in ("apt-get", "apt"):
        return ["sudo", pm, "install", "-y", package]
    if pm in ("dnf", "yum"):
        return ["sudo", pm, "install", "-y", package]
    if pm == "brew":
        return ["brew", "install", package]
    return []


def install_tool(name: str) -> bool:
    """Attempt to install a tool via the system package manager. Returns True on success."""
    spec = TOOLS_BY_NAME.get(name)
    if spec is None:
        logger.warning("deps: unknown tool '%s'", name)
        return False

    pm = _detect_pkg_manager()
    if pm is None:
        logger.error("deps: no supported package manager found (apt/dnf/yum/brew)")
        if spec.install_note:
            logger.error("deps: manual install: %s", spec.install_note)
        return False

    pkg: Optional[str] = None
    if pm in ("apt-get", "apt"):
        pkg = spec.apt
    elif pm in ("dnf", "yum"):
        pkg = spec.yum
    elif pm == "brew":
        pkg = spec.brew

    if pkg is None:
        if spec.install_note:
            logger.warning(
                "deps: no package available for '%s' via %s — manual install: %s",
                name, pm, spec.install_note,
            )
        else:
            logger.error("deps: no package known for '%s' via %s", name, pm)
        return False

    cmd = _build_install_cmd(pm, pkg)
    logger.info("deps: installing %s via: %s", name, " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode == 0:
            logger.info("deps: successfully installed %s", name)
            return True
        logger.error("deps: failed to install %s (exit %d): %s", name, result.returncode, result.stderr.strip())
        return False
    except (subprocess.TimeoutExpired, OSError) as exc:
        logger.error("deps: error installing %s: %s", name, exc)
        return False


# ---------------------------------------------------------------------------
# Startup gate
# ---------------------------------------------------------------------------

def ensure_required(auto_install: bool = False) -> bool:
    """
    Check all required tools. If auto_install=True, try to install anything missing.
    Logs warnings for optional tools. Returns True if all required tools are present
    (after any install attempts).
    """
    result = audit()
    logger.info("deps: tool audit\n%s", result.report())

    if result.ok:
        return True

    for name in result.missing_required:
        spec = TOOLS_BY_NAME[name]
        if auto_install:
            logger.warning("deps: required tool '%s' missing — attempting auto-install", name)
            if not install_tool(name):
                logger.error(
                    "deps: could not auto-install '%s' (%s) — install manually and restart",
                    name, spec.description,
                )
        else:
            hint = f"sudo apt install {spec.apt}" if spec.apt else (spec.install_note or f"install {name}")
            logger.error(
                "deps: required tool '%s' not found (%s). Install with: %s",
                name, spec.description, hint,
            )

    # Re-check after any install attempts
    still_missing = [n for n in result.missing_required if not check_tool(n)]
    if still_missing:
        logger.warning(
            "deps: ZeroDaemon will have limited functionality — missing required tools: %s",
            ", ".join(still_missing),
        )
        return False
    return True
