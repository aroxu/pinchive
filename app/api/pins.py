"""Pin endpoints: listing within a board, tagging, bulk actions."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from app.api.schemas import BulkTagIn, PageMeta, PageOut, PinOut, TagIn
from app.services import board_service, pin_service, settings_service

router = APIRouter(prefix="/api", tags=["pins"])


@router.get("/boards/{board_id}/pins", response_model=PageOut[PinOut])
def list_pins(
    board_id: int,
    q: str = Query(default=""),
    type: str = Query(default=""),
    dupes: bool = Query(default=False),
    sort: str = Query(default=pin_service.DEFAULT_SORT),
    page: int = Query(default=1, ge=1),
):
    if board_service.get_board(board_id) is None:
        raise HTTPException(404, "board not found")
    result = pin_service.list_pins(
        board_id, q=q, media_type=type, dupes_only=dupes, sort=sort, page=page,
        per_page=settings_service.get("per_page_pins"),
    )
    tags = pin_service.pin_tags([p.id for p in result.items])
    return PageOut[PinOut](
        items=[PinOut.of(p, tags=tags.get(p.id, [])) for p in result.items],
        meta=PageMeta(page=result.page, pages=result.pages, total=result.total),
    )


@router.post("/pins/{pin_id}/tags", response_model=PinOut)
def add_tag(pin_id: int, payload: TagIn):
    pin = pin_service.get_pin(pin_id)
    if pin is None:
        raise HTTPException(404, "pin not found")
    pin_service.tag_pins([pin_id], payload.name)
    return PinOut.of(pin, tags=pin_service.pin_tags([pin_id]).get(pin_id, []))


@router.delete("/pins/{pin_id}/tags/{tag_id}", response_model=PinOut)
def remove_tag(pin_id: int, tag_id: int):
    pin = pin_service.get_pin(pin_id)
    if pin is None:
        raise HTTPException(404, "pin not found")
    pin_service.untag_pin_by_id(pin_id, tag_id)
    return PinOut.of(pin, tags=pin_service.pin_tags([pin_id]).get(pin_id, []))


@router.post("/pins/bulk")
def bulk(payload: BulkTagIn):
    """Apply/remove a tag across many pins, or bulk-delete them."""
    if not payload.pin_ids:
        return {"affected": 0}
    if payload.action == "delete":
        return {"affected": pin_service.delete_pins(payload.pin_ids)}
    if not payload.name.strip():
        raise HTTPException(400, "tag name required")
    affected = pin_service.tag_pins(
        payload.pin_ids, payload.name, remove=payload.action == "remove"
    )
    return {"affected": affected}


@router.get("/tags")
def list_tags():
    return [{"id": t.id, "name": t.name} for t in board_service.list_tags()]
