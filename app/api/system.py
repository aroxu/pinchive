"""Settings, stats, and health endpoints."""

from __future__ import annotations

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Request

from app.api.deps import enqueue, get_queue
from app.api.schemas import (
    HealthOut,
    SettingsOut,
    SidebarCountsOut,
    StatsOut,
)
from app.core.config import get_settings
from app.services import board_service, settings_service

router = APIRouter(prefix="/api", tags=["system"])
settings = get_settings()


@router.get("/stats", response_model=StatsOut)
def stats():
    return StatsOut(**board_service.archive_stats())


@router.get("/sidebar", response_model=SidebarCountsOut)
def sidebar():
    return SidebarCountsOut(**board_service.sidebar_counts())


@router.get("/settings", response_model=SettingsOut)
def read_settings(queue: ArqRedis | None = Depends(get_queue)):
    return SettingsOut(
        values=settings_service.effective(),
        defaults=settings_service.defaults(),
        editable=list(settings_service.EDITABLE),
        # Worker parallelism is process-level: it needs a restart, so it is env
        # only and surfaced read-only here.
        max_concurrency=settings.max_concurrency,
        queue_up=queue is not None,
    )


@router.put("/settings", response_model=SettingsOut)
def write_settings(payload: dict, queue: ArqRedis | None = Depends(get_queue)):
    settings_service.save(payload)
    return read_settings(queue)


@router.post("/settings/sync-all")
async def sync_all(queue: ArqRedis | None = Depends(get_queue)):
    """Manually enqueue an auto-resync of all opted-in boards."""
    return {"queued": await enqueue(queue, "resync_all_boards")}


@router.get("/healthz", response_model=HealthOut)
def healthz(request: Request):
    return HealthOut(
        status="ok", queue=getattr(request.app.state, "arq", None) is not None
    )
