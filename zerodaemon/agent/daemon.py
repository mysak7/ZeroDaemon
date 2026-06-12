"""Background daemon loop — runs scheduled scans and keeps the agent alive."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from langchain_core.messages import HumanMessage

logger = logging.getLogger(__name__)


@dataclass
class DaemonState:
    status: str = "idle"          # "idle" | "running" | "paused" | "stopped"
    last_run_ts: Optional[str] = None
    last_target: Optional[str] = None
    error: Optional[str] = None
    scheduled_targets: list[str] = field(default_factory=list)


_state = DaemonState()
_stop_event: Optional[asyncio.Event] = None
_wake_event: Optional[asyncio.Event] = None
_task: Optional[asyncio.Task] = None
_checkpointer = None
_db_path: str = "zerodaemon.db"


async def _load_targets() -> list[str]:
    from zerodaemon.db.sqlite import get_db
    async with get_db(_db_path) as conn:
        async with conn.execute("SELECT ip FROM daemon_targets ORDER BY rowid") as cur:
            rows = await cur.fetchall()
    return [r["ip"] for r in rows]


async def _persist_target_add(ip: str) -> None:
    from zerodaemon.db.sqlite import get_db
    async with get_db(_db_path) as conn:
        await conn.execute("INSERT OR IGNORE INTO daemon_targets (ip) VALUES (?)", (ip,))
        await conn.commit()


async def _persist_target_remove(ip: str) -> None:
    from zerodaemon.db.sqlite import get_db
    async with get_db(_db_path) as conn:
        await conn.execute("DELETE FROM daemon_targets WHERE ip = ?", (ip,))
        await conn.commit()


async def _run_scan(registry, target: str) -> None:
    """Invoke the agent graph for a single target IP."""
    from zerodaemon.agent.graph import build_graph

    _state.status = "running"
    _state.last_target = target

    try:
        graph, _ = build_graph(registry, _checkpointer)
        config = {"configurable": {"thread_id": f"daemon_{target}"}}
        prompt = (
            f"Initiate your daily security routine for IP: {target}. "
            "1. Check the historical database for what was open previously. "
            "2. Run a live Nmap scan via scan_services. If it reports needs_worker, "
            "use manage_infrastructure to build one small spot worker, then retry. "
            "3. If there are new ports or changed versions, search for recent CVEs. "
            "Report any anomalies or drift you detect."
        )
        async for _ in graph.astream(
            {"messages": [HumanMessage(content=prompt)], "thread_id": f"daemon_{target}", "active_model_id": ""},
            config=config,
            stream_mode="values",
        ):
            pass  # Results stored in LangGraph checkpointer

        _state.last_run_ts = datetime.now(timezone.utc).isoformat()
        _state.error = None
        logger.info("Daemon scan completed for %s", target)
    except Exception as exc:
        _state.error = str(exc)
        logger.exception("Daemon scan failed for %s: %s", target, exc)
    finally:
        _state.status = "idle"


async def _loop(registry) -> None:
    from zerodaemon.core.config import get_settings

    logger.info("ZeroDaemon background loop started")
    while not _stop_event.is_set():
        settings = get_settings()

        if settings.daemon_paused or not _state.scheduled_targets:
            _state.status = "paused" if settings.daemon_paused else "idle"
            try:
                await asyncio.wait_for(_wake_event.wait(), timeout=60.0)
            except asyncio.TimeoutError:
                pass
            _wake_event.clear()
            continue

        for target in list(_state.scheduled_targets):
            if _stop_event.is_set():
                break
            await _run_scan(registry, target)

        # Sleep until next poll interval or until woken
        try:
            await asyncio.wait_for(
                _wake_event.wait(),
                timeout=float(settings.daemon_poll_interval),
            )
        except asyncio.TimeoutError:
            pass
        _wake_event.clear()

    _state.status = "stopped"
    logger.info("ZeroDaemon background loop stopped")


async def start(registry, checkpointer=None, db_path: str = "zerodaemon.db") -> None:
    global _stop_event, _wake_event, _task, _checkpointer, _db_path
    _checkpointer = checkpointer
    _db_path = db_path
    _stop_event = asyncio.Event()
    _wake_event = asyncio.Event()

    # Load persisted targets from DB
    try:
        targets = await _load_targets()
        _state.scheduled_targets = targets
        logger.info("Daemon loaded %d persisted target(s)", len(targets))
    except Exception as exc:
        logger.warning("Could not load persisted daemon targets: %s", exc)

    _task = asyncio.create_task(_loop(registry), name="zerodaemon-loop")


async def stop() -> None:
    if _stop_event:
        _stop_event.set()
    if _wake_event:
        _wake_event.set()
    if _task:
        await asyncio.wait_for(_task, timeout=5.0)


def wake() -> None:
    """Wake the daemon loop immediately (e.g., after adding a new target)."""
    if _wake_event:
        _wake_event.set()


def get_state() -> DaemonState:
    return _state


async def add_target(ip: str) -> None:
    if ip not in _state.scheduled_targets:
        _state.scheduled_targets.append(ip)
        await _persist_target_add(ip)
        wake()


async def remove_target(ip: str) -> None:
    _state.scheduled_targets = [t for t in _state.scheduled_targets if t != ip]
    await _persist_target_remove(ip)
