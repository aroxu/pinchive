"""Pin queries, sorting, tagging, and deletion."""

from __future__ import annotations

from peewee import fn

from app.core.db import connection, transaction
from app.models import Board, DeletedPin, Pin, PinTagLink, Tag
from app.services.board_service import Page, now
from app.services.storage import get_storage

# Recency = the Pinterest pin id, which is time-ordered (higher id = added more
# recently) and is embedded in the filename `pinterest_<pinid>_…`. We sort on the
# filename rather than the row id because the row id is reassigned when a board
# is re-synced (the reconcile re-inserts by filename), which would flip the
# order; the filename is stable. Newest = filename descending.
_AREA = Pin.width * Pin.height

PIN_SORTS = {
    "new": [Pin.filename.desc()],
    "old": [Pin.filename.asc()],
    "big": [_AREA.desc(), Pin.filename.desc()],
    "small": [_AREA.asc(), Pin.filename.asc()],
    "name": [Pin.filename.asc(), Pin.id.asc()],
}
DEFAULT_SORT = "new"


def list_pins(
    board_id: int,
    *,
    q: str = "",
    media_type: str = "",
    dupes_only: bool = False,
    sort: str = DEFAULT_SORT,
    page: int = 1,
    per_page: int = 60,
) -> Page:
    sort = sort if sort in PIN_SORTS else DEFAULT_SORT
    with connection():
        query = Pin.select().where(Pin.board == board_id)
        q = (q or "").strip()
        if q:
            like = f"%{q}%"
            query = query.where(
                (Pin.filename ** like)
                | (Pin.title ** like)
                | (Pin.description ** like)
                | (Pin.source_url ** like)
            )
        if media_type in ("image", "video"):
            query = query.where(Pin.media_type == media_type)
        if dupes_only:
            query = query.where(Pin.dup_group.is_null(False))
        total = query.count()
        pages = max(1, -(-total // per_page))
        page = min(max(1, page), pages)
        items = list(query.order_by(*PIN_SORTS[sort]).paginate(page, per_page))
    return Page(items=items, page=page, pages=pages, total=total)


def get_pin(pin_id: int) -> Pin | None:
    with connection():
        return Pin.get_or_none(Pin.id == pin_id)


def pin_tags(pin_ids: list[int]) -> dict[int, list[str]]:
    """Tag names per pin id, in one query (avoids N+1 on the grid)."""
    if not pin_ids:
        return {}
    out: dict[int, list[str]] = {}
    with connection():
        rows = (
            PinTagLink.select(PinTagLink.pin, Tag.name)
            .join(Tag, on=(PinTagLink.tag == Tag.id))
            .where(PinTagLink.pin.in_(pin_ids))
            .tuples()
        )
        for pid, name in rows:
            out.setdefault(pid, []).append(name)
    return out


def board_tags(board_ids: list[int]) -> dict[int, list[str]]:
    from app.models import BoardTagLink

    if not board_ids:
        return {}
    out: dict[int, list[str]] = {}
    with connection():
        rows = (
            BoardTagLink.select(BoardTagLink.board, Tag.name)
            .join(Tag, on=(BoardTagLink.tag == Tag.id))
            .where(BoardTagLink.board.in_(board_ids))
            .tuples()
        )
        for bid, name in rows:
            out.setdefault(bid, []).append(name)
    return out


# --------------------------------------------------------------------------- #
# tags
# --------------------------------------------------------------------------- #
def get_or_create_tag(name: str) -> Tag | None:
    name = (name or "").strip().lower()
    if not name:
        return None
    with transaction():
        tag, _ = Tag.get_or_create(name=name)
        return tag


def tag_pins(pin_ids: list[int], name: str, *, remove: bool = False) -> int:
    tag = get_or_create_tag(name)
    if tag is None or not pin_ids:
        return 0
    with transaction():
        if remove:
            return (
                PinTagLink.delete()
                .where((PinTagLink.tag == tag.id) & (PinTagLink.pin.in_(pin_ids)))
                .execute()
            )
        rows = [{"pin": pid, "tag": tag.id} for pid in pin_ids]
        # Re-tagging an already-tagged pin is a no-op, not an error.
        return PinTagLink.insert_many(rows).on_conflict_ignore().execute() or 0


def tag_board(board_id: int, name: str, *, remove: bool = False) -> bool:
    from app.models import BoardTagLink

    tag = get_or_create_tag(name)
    if tag is None:
        return False
    with transaction():
        if remove:
            BoardTagLink.delete().where(
                (BoardTagLink.board == board_id) & (BoardTagLink.tag == tag.id)
            ).execute()
        else:
            BoardTagLink.insert(board=board_id, tag=tag.id).on_conflict_ignore().execute()
    return True


def untag_board_by_id(board_id: int, tag_id: int) -> None:
    from app.models import BoardTagLink

    with transaction():
        BoardTagLink.delete().where(
            (BoardTagLink.board == board_id) & (BoardTagLink.tag == tag_id)
        ).execute()


def untag_pin_by_id(pin_id: int, tag_id: int) -> None:
    with transaction():
        PinTagLink.delete().where(
            (PinTagLink.pin == pin_id) & (PinTagLink.tag == tag_id)
        ).execute()


# --------------------------------------------------------------------------- #
# deletion
# --------------------------------------------------------------------------- #
def delete_pins(pin_ids: list[int], *, tombstone: bool = False) -> int:
    """Delete pins with their media. ``tombstone`` records the path so a board
    re-sync never re-adds it (used when removing duplicates)."""
    if not pin_ids:
        return 0
    storage = get_storage()
    deleted = 0
    with transaction():
        pins = list(Pin.select().where(Pin.id.in_(pin_ids)))
        if not pins:
            return 0
        if tombstone:
            DeletedPin.insert_many(
                [{"rel_path": p.rel_path, "deleted_at": now()} for p in pins]
            ).on_conflict_ignore().execute()
        for p in pins:
            storage.delete(p.rel_path)
        deleted = Pin.delete().where(Pin.id.in_([p.id for p in pins])).execute()
        for board_id in {p.board_id for p in pins}:
            _reconcile_board_counts(board_id)
    return deleted


def _reconcile_board_counts(board_id: int) -> None:
    """After deleting pins, keep the board's counters honest with disk."""
    board = Board.get_or_none(Board.id == board_id)
    if board is None:
        return
    remaining = Pin.select(fn.COUNT(Pin.id)).where(Pin.board == board_id).scalar() or 0
    board.pin_count = remaining
    board.downloaded_count = min(board.downloaded_count or 0, remaining)
    board.save()
