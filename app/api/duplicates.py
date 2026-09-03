"""Duplicate endpoints: read stored groups, rescan, resolve."""

from __future__ import annotations

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, Query

from app.api.deps import enqueue, get_queue
from app.api.schemas import (
    DuplicateGroupOut,
    PageMeta,
    PageOut,
    PinOut,
    ResolveDuplicatesIn,
)
from app.services import dedup_service, pin_service, settings_service

router = APIRouter(prefix="/api/duplicates", tags=["duplicates"])


@router.get("", response_model=PageOut[DuplicateGroupOut])
def list_duplicates(page: int = Query(default=1, ge=1)):
    result, _total_extra = dedup_service.duplicate_page(
        page, settings_service.get("per_page_dupes")
    )
    groups = [
        DuplicateGroupOut(
            # group[0] is the highest-resolution copy: the one we keep.
            group_id=group[0].dup_group,
            keeper_id=group[0].id,
            pins=[PinOut.of(p) for p in group],
        )
        for group in result.items
    ]
    return PageOut[DuplicateGroupOut](
        items=groups,
        meta=PageMeta(page=result.page, pages=result.pages, total=result.total),
    )


@router.get("/status")
def status():
    """Live progress of the rescan job (polled while it runs)."""
    st = dedup_service.get_status()
    groups = dedup_service.stored_groups()
    return {
        **st,
        "total_groups": len(groups),
        "removable": sum(len(g) - 1 for g in groups),
    }


@router.post("/rescan")
async def rescan(queue: ArqRedis | None = Depends(get_queue)):
    """Trigger the (re)hash + regroup pass; results are stored in the DB."""
    # Optimistically mark "queued" so the UI shows live status immediately,
    # before the worker actually picks the job up.
    dedup_service.set_status(running=True, phase="queued")
    queued = await enqueue(queue, "recompute_duplicates")
    if not queued:
        dedup_service.set_status(running=False, phase="idle")
    return {"queued": queued}


@router.post("/resolve")
def resolve(payload: ResolveDuplicatesIn):
    """Delete duplicate copies. ``all=true`` keeps the highest-resolution copy
    in every group and removes the rest."""
    pin_ids = (
        dedup_service.redundant_pin_ids() if payload.all else payload.pin_ids
    )
    deleted = pin_service.delete_pins(pin_ids, tombstone=True)
    return {"deleted": deleted}
