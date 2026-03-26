"""SQLite-backed LLM usage tracking — mirrors seip-models llm_log pattern."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Optional

from zerodaemon.db.sqlite import get_db
from zerodaemon.models.schemas import ModelEntry, UsageRecord, UsageStats


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


async def record_start(
    db_path: str,
    model: ModelEntry,
    caller: str = "unknown",
    thread_id: Optional[str] = None,
) -> tuple[str, float]:
    """
    Insert a 'running' record when an LLM call begins.
    Returns (entry_id, start_time) — pass both to record_end().
    """
    entry_id = str(uuid.uuid4())
    async with get_db(db_path) as conn:
        await conn.execute(
            """
            INSERT INTO llm_usage (id, ts, model_id, provider, caller, thread_id, status)
            VALUES (?, ?, ?, ?, ?, ?, 'running')
            """,
            (entry_id, _now_iso(), model.id, model.provider, caller, thread_id),
        )
        await conn.commit()
    return entry_id, time.monotonic()


async def record_end(
    db_path: str,
    entry_id: str,
    start_time: float,
    model: ModelEntry,
    input_tokens: Optional[int] = None,
    output_tokens: Optional[int] = None,
    error: Optional[str] = None,
) -> None:
    """Update an existing usage record with completion data and computed cost."""
    duration_ms = int((time.monotonic() - start_time) * 1000)
    cost_usd: Optional[float] = None
    if input_tokens is not None and output_tokens is not None:
        cost_usd = (
            input_tokens * model.input_mtok / 1_000_000
            + output_tokens * model.output_mtok / 1_000_000
        )
    status = "error" if error else "ok"
    async with get_db(db_path) as conn:
        await conn.execute(
            """
            UPDATE llm_usage
            SET input_tokens=?, output_tokens=?, cost_usd=?, duration_ms=?, status=?, error=?
            WHERE id=?
            """,
            (input_tokens, output_tokens, cost_usd, duration_ms, status, error, entry_id),
        )
        await conn.commit()


async def get_log(
    db_path: str,
    limit: int = 100,
    model_id: Optional[str] = None,
) -> list[UsageRecord]:
    """Fetch recent usage records, optionally filtered by model_id."""
    async with get_db(db_path) as conn:
        if model_id:
            cursor = await conn.execute(
                "SELECT * FROM llm_usage WHERE model_id=? ORDER BY ts DESC LIMIT ?",
                (model_id, limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT * FROM llm_usage ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
    return [UsageRecord(**dict(r)) for r in rows]


async def get_stats(db_path: str) -> UsageStats:
    """Return aggregate usage statistics across all models."""
    async with get_db(db_path) as conn:
        cursor = await conn.execute(
            """
            SELECT
                COUNT(*) as total_calls,
                COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                COALESCE(SUM(cost_usd), 0.0) as total_cost_usd
            FROM llm_usage WHERE status='ok'
            """
        )
        row = await cursor.fetchone()
        totals = dict(row)

        cursor2 = await conn.execute(
            """
            SELECT model_id, provider,
                   COUNT(*) as calls,
                   COALESCE(SUM(input_tokens), 0) as input_tokens,
                   COALESCE(SUM(output_tokens), 0) as output_tokens,
                   COALESCE(SUM(cost_usd), 0.0) as cost_usd
            FROM llm_usage WHERE status='ok'
            GROUP BY model_id, provider
            ORDER BY calls DESC
            """
        )
        by_model = [dict(r) for r in await cursor2.fetchall()]

    return UsageStats(
        total_calls=totals["total_calls"],
        total_input_tokens=totals["total_input_tokens"],
        total_output_tokens=totals["total_output_tokens"],
        total_cost_usd=totals["total_cost_usd"],
        by_model=by_model,
    )
