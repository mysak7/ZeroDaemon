"""Workers API — build, list, and destroy cloud worker VMs.

Mirrors the /models pattern: a thin REST layer over the WorkerManager singleton,
so workers are visible and emergency-destroyable from the UI/curl, not only via
the agent. Blocking cloud calls are offloaded to threads.
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, HTTPException

from zerodaemon.workers import manager
from zerodaemon.workers.base import WorkerProviderError
from zerodaemon.workers.schemas import ProviderInfo, WorkerCreate, WorkerRecord

router = APIRouter()


def _mgr() -> manager.WorkerManager:
    m = manager.get_manager()
    if m is None:
        raise HTTPException(status_code=503, detail="Worker manager not initialised")
    return m


@router.get("", response_model=list[WorkerRecord], summary="List worker VMs")
async def list_workers(include_terminated: bool = False) -> list[WorkerRecord]:
    return await asyncio.to_thread(_mgr().list_workers, include_terminated)


@router.post("", response_model=WorkerRecord, status_code=201, summary="Build a worker VM")
async def create_worker(body: WorkerCreate) -> WorkerRecord:
    try:
        return await asyncio.to_thread(_mgr().create_worker, body)
    except WorkerProviderError as exc:
        raise HTTPException(status_code=409, detail=str(exc))


@router.get("/providers", response_model=list[ProviderInfo], summary="List worker providers")
async def list_providers() -> list[ProviderInfo]:
    return await asyncio.to_thread(_mgr().provider_infos)


@router.post("/reconcile", summary="Reconcile DB workers against the cloud")
async def reconcile() -> dict:
    return await asyncio.to_thread(_mgr().reconcile)


@router.get("/{worker_id}", response_model=WorkerRecord, summary="Get a worker")
async def get_worker(worker_id: str) -> WorkerRecord:
    rec = await asyncio.to_thread(_mgr().get_worker, worker_id)
    if rec is None:
        raise HTTPException(status_code=404, detail=f"Worker '{worker_id}' not found")
    return rec


@router.delete("/{worker_id}", response_model=WorkerRecord, summary="Destroy a worker VM")
async def destroy_worker(worker_id: str) -> WorkerRecord:
    try:
        return await asyncio.to_thread(_mgr().destroy_worker, worker_id)
    except WorkerProviderError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
