"""Scans API — view historical scan results."""

from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from zerodaemon.api.deps import get_settings_dep
from zerodaemon.core.config import Settings
from zerodaemon.db.sqlite import get_db

router = APIRouter()


class ScanRecord(BaseModel):
    id: str
    ts: str
    target: str
    scan_type: str
    summary: Optional[str]
    raw_json: Optional[str] = None  # omitted in list view


@router.get("", response_model=list[ScanRecord], summary="List recent scans")
async def list_scans(
    target: Optional[str] = Query(None, description="Filter by target IP"),
    limit: int = Query(50, ge=1, le=500),
    settings: Settings = Depends(get_settings_dep),
) -> list[ScanRecord]:
    async with get_db(settings.db_path) as conn:
        if target:
            cursor = await conn.execute(
                "SELECT id, ts, target, scan_type, summary FROM scans WHERE target=? ORDER BY ts DESC LIMIT ?",
                (target, limit),
            )
        else:
            cursor = await conn.execute(
                "SELECT id, ts, target, scan_type, summary FROM scans ORDER BY ts DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
    return [ScanRecord(**dict(r)) for r in rows]


@router.get("/{scan_id}", response_model=ScanRecord, summary="Get scan detail with raw JSON")
async def get_scan(
    scan_id: str,
    settings: Settings = Depends(get_settings_dep),
) -> ScanRecord:
    async with get_db(settings.db_path) as conn:
        cursor = await conn.execute(
            "SELECT * FROM scans WHERE id=?",
            (scan_id,),
        )
        row = await cursor.fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"Scan '{scan_id}' not found")
    return ScanRecord(**dict(row))
