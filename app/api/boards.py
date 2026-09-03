"""Board endpoints."""

from __future__ import annotations

from arq.connections import ArqRedis
from fastapi import APIRouter, Depends, HTTPException, Query

from app.api.deps import enqueue, get_queue
from app.api.schemas import BoardCreateIn, BoardOut, PageMeta, PageOut, TagIn
from app.services import board_service, pin_service, settings_service

router = APIRouter(prefix="/api/boards", tags=["boards"])

_QUEUE_DOWN = "queue unavailable — press retry once the worker is up"


def _serialize(board, *, tags=None, include_log=False) -> BoardOut:
    return BoardOut.of(
        board,
        covers=board_service.board_covers(board.id),
        tags=tags,
        include_log=include_log,
    )


@router.get("", response_model=PageOut[BoardOut])
def list_boards(
    q: str = Query(default=""),
    status: str = Query(default=""),
    tag: str = Query(default=""),
    page: int = Query(default=1, ge=1),
):
    result = board_service.list_boards(
        q=q, status=status, tag=tag, page=page,
        per_page=settings_service.get("per_page_boards"),
    )
    tags = pin_service.board_tags([b.id for b in result.items])
    return PageOut[BoardOut](
        items=[_serialize(b, tags=tags.get(b.id, [])) for b in result.items],
        meta=PageMeta(page=result.page, pages=result.pages, total=result.total),
    )


@router.post("", response_model=BoardOut, status_code=201)
async def create_board(
    payload: BoardCreateIn, queue: ArqRedis | None = Depends(get_queue)
):
    url = payload.url.strip()
    if "pinterest." not in url:
        raise HTTPException(400, "not a Pinterest URL")
    board = board_service.create_board(
        url, credential_id=payload.credential_id, auto=payload.auto
    )
    if not await enqueue(queue, "download_board", board.id):
        board = board_service.mark_unqueueable(board.id, _QUEUE_DOWN) or board
    return _serialize(board)


@router.get("/{board_id}", response_model=BoardOut)
def get_board(board_id: int):
    board = board_service.get_board(board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    tags = pin_service.board_tags([board_id]).get(board_id, [])
    return _serialize(board, tags=tags, include_log=True)


@router.post("/{board_id}/resync", response_model=BoardOut)
async def resync_board(
    board_id: int,
    full: bool = Query(default=False),
    queue: ArqRedis | None = Depends(get_queue),
):
    """Queue a sync. ``full=true`` bypasses the ten-pin checkpoint and rescans
    the board end to end."""
    board = board_service.get_board(board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    if board.is_active:
        return _serialize(board)  # already in flight — never double-queue
    board = board_service.mark_queued(board_id, force_full=full) or board
    args = (board_id, True) if full else (board_id,)
    if not await enqueue(queue, "download_board", *args):
        board = board_service.mark_unqueueable(board_id, "queue unavailable") or board
    return _serialize(board)


@router.post("/{board_id}/auto-resync", response_model=BoardOut)
def toggle_auto_resync(board_id: int):
    board = board_service.toggle_auto_resync(board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    return _serialize(board)


@router.delete("/{board_id}", status_code=204)
def delete_board(board_id: int):
    if not board_service.delete_board(board_id):
        raise HTTPException(404, "board not found")


@router.post("/{board_id}/tags", response_model=BoardOut)
def add_tag(board_id: int, payload: TagIn):
    board = board_service.get_board(board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    pin_service.tag_board(board_id, payload.name)
    tags = pin_service.board_tags([board_id]).get(board_id, [])
    return _serialize(board, tags=tags)


@router.delete("/{board_id}/tags/{tag_id}", response_model=BoardOut)
def remove_tag(board_id: int, tag_id: int):
    board = board_service.get_board(board_id)
    if board is None:
        raise HTTPException(404, "board not found")
    pin_service.untag_board_by_id(board_id, tag_id)
    tags = pin_service.board_tags([board_id]).get(board_id, [])
    return _serialize(board, tags=tags)
