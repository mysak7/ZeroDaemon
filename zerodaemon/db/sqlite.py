"""SQLite database initialisation and connection factory."""

from contextlib import asynccontextmanager
from typing import AsyncIterator

import aiosqlite


_CREATE_TABLES = [
    # LLM usage log — mirrors seip-models llm_log table but in SQLite
    """
    CREATE TABLE IF NOT EXISTS llm_usage (
        id            TEXT PRIMARY KEY,
        ts            TEXT NOT NULL,
        model_id      TEXT NOT NULL,
        provider      TEXT NOT NULL,
        caller        TEXT NOT NULL DEFAULT 'unknown',
        thread_id     TEXT,
        input_tokens  INTEGER,
        output_tokens INTEGER,
        cost_usd      REAL,
        duration_ms   INTEGER,
        status        TEXT NOT NULL DEFAULT 'running',
        error         TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_llm_usage_ts    ON llm_usage(ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_llm_usage_model ON llm_usage(model_id)",

    # Scan results stored by the scan_services tool
    """
    CREATE TABLE IF NOT EXISTS scans (
        id         TEXT PRIMARY KEY,
        ts         TEXT NOT NULL,
        target     TEXT NOT NULL,
        scan_type  TEXT NOT NULL DEFAULT 'service',
        raw_json   TEXT,
        summary    TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_scans_ts     ON scans(ts DESC)",
    "CREATE INDEX IF NOT EXISTS idx_scans_target ON scans(target)",

    # Local threat intel cache
    """
    CREATE TABLE IF NOT EXISTS threat_intel (
        indicator      TEXT PRIMARY KEY,
        indicator_type TEXT NOT NULL DEFAULT 'ip',
        fetched_ts     TEXT NOT NULL,
        data_json      TEXT,
        verdict        TEXT
    )
    """,
]


async def init_tables(db_path: str) -> None:
    """Create all application tables and indexes if they don't exist."""
    async with aiosqlite.connect(db_path) as conn:
        await conn.execute("PRAGMA journal_mode=WAL")
        await conn.execute("PRAGMA foreign_keys=ON")
        for stmt in _CREATE_TABLES:
            await conn.execute(stmt)
        await conn.commit()


@asynccontextmanager
async def get_db(db_path: str) -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager yielding a connected aiosqlite connection."""
    async with aiosqlite.connect(db_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.execute("PRAGMA journal_mode=WAL")
        yield conn
