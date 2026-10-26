"""Board lifecycle: creation, listing/filtering, sync bookkeeping, deletion."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse

from peewee import fn

from app.core.db import connection, transaction
from app.models import (
    ACTIVE_STATUSES,
    STATUS_GROUPS,
    Board,
    BoardStatus,
    BoardTagLink,
    Credential,
    CredentialStatus,
    DeletedPin,
    Pin,
    Tag,
)

# The size of the newest-first pin-id window is defined by the downloader, which
# emits it; re-exported here because callers think of it as sync policy.
from app.services.downloader import SYNC_CHECKPOINT_SIZE
from app.services.storage import get_storage

_RESYNCABLE = (BoardStatus.done, BoardStatus.partial, BoardStatus.error)
_INTERRUPTED = (BoardStatus.downloading, BoardStatus.queued, BoardStatus.pending)


def now() -> datetime:
    return datetime.now(UTC)


def derive_slug(url: str) -> str:
    """``pinterest.com/<user>/<board>/`` -> ``user__board``; else a safe fallback."""
    path = urlparse(url).path.strip("/")
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        slug = f"{parts[0]}__{parts[1]}"
    elif parts:
        slug = parts[0]
    else:
        slug = "board"
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", slug).strip("-")
    return slug or "board"


def board_folder(slug: str, board_id: int) -> str:
    """Per-board directory name. The id suffix guarantees uniqueness so two
    boards with the same derived slug never share a folder."""
    return f"{slug}-{board_id}"


@dataclass
class Page:
    items: list
    page: int
    pages: int
    total: int


# --------------------------------------------------------------------------- #
# queries
# --------------------------------------------------------------------------- #
def list_boards(
    *,
    q: str = "",
    status: str = "",
    tag: str = "",
    page: int = 1,
    per_page: int = 24,
) -> Page:
    with connection():
        query = Board.select()
        q = (q or "").strip()
        if q:
            like = f"%{q}%"
            query = query.where(
                (Board.title ** like) | (Board.url ** like) | (Board.slug ** like)
            )
        if status in STATUS_GROUPS:
            query = query.where(
                Board.status.in_([s.value for s in STATUS_GROUPS[status]])
            )
        tag = (tag or "").strip()
        if tag:
            query = (
                query.join(BoardTagLink, on=(BoardTagLink.board == Board.id))
                .join(Tag, on=(BoardTagLink.tag == Tag.id))
                .where(Tag.name == tag)
            )
        total = query.count()
        pages = max(1, -(-total // per_page))
        page = min(max(1, page), pages)
        items = list(
            query.order_by(Board.created_at.desc(), Board.id.desc())
            .paginate(page, per_page)
        )
    return Page(items=items, page=page, pages=pages, total=total)


def get_board(board_id: int) -> Board | None:
    with connection():
        return Board.get_or_none(Board.id == board_id)


def any_active(boards: list[Board]) -> bool:
    active = {s.value for s in ACTIVE_STATUSES}
    return any(b.status in active for b in boards)


def archive_stats() -> dict:
    """Archive-wide totals for the dashboard header."""
    with connection():
        return {
            "boards": Board.select().count(),
            "pins": Pin.select().count(),
            "sessions": Credential.select().count(),
        }


def sidebar_counts() -> dict:
    """Nav counters: boards, sessions, in-flight syncs, removable duplicates."""
    with connection():
        boards = Board.select().count()
        creds = Credential.select().count()
        creds_active = (
            Credential.select()
            .where(Credential.status == CredentialStatus.active.value)
            .count()
        )
        syncing = (
            Board.select()
            .where(Board.status.in_([s.value for s in ACTIVE_STATUSES]))
            .count()
        )
        in_groups = Pin.select().where(Pin.dup_group.is_null(False)).count()
        group_count = (
            Pin.select(fn.COUNT(fn.DISTINCT(Pin.dup_group)))
            .where(Pin.dup_group.is_null(False))
            .scalar()
            or 0
        )
    return {
        "boards": boards,
        "creds": creds,
        "creds_active": creds_active,
        "syncing": syncing,
        # Copies beyond one-per-group: what "delete duplicates" would remove.
        "removable": max(0, in_groups - group_count),
    }


def board_covers(board_id: int, n: int = 4) -> list[str]:
    """Up to n image rel_paths for a board's collage cover."""
    with connection():
        return [
            p.rel_path
            for p in Pin.select(Pin.rel_path)
            .where((Pin.board == board_id) & (Pin.media_type == "image"))
            .order_by(Pin.filename)
            .limit(n)
        ]


def list_tags() -> list[Tag]:
    with connection():
        return list(Tag.select().order_by(Tag.name))


# --------------------------------------------------------------------------- #
# mutations
# --------------------------------------------------------------------------- #
def create_board(url: str, *, credential_id: int | None, auto: bool = False) -> Board:
    """Register a board as queued. ``auto`` picks the freshest live session so
    private boards work without the user choosing one (cookies are harmless on
    public boards)."""
    url = url.strip()
    with transaction():
        if auto and credential_id is None:
            cred = (
                Credential.select()
                .where(Credential.status == CredentialStatus.active.value)
                .order_by(Credential.last_checked_at.desc())
                .first()
            )
            credential_id = cred.id if cred else None
        return Board.create(
            url=url,
            slug=derive_slug(url),
            status=BoardStatus.queued.value,
            credential=credential_id,
        )


def mark_queued(board_id: int, *, force_full: bool | None = False) -> Board | None:
    """Flip a board to queued ahead of enqueueing. ``force_full`` is persisted so
    a worker restart resumes the full scan instead of the incremental one.
    Pass ``None`` when resuming to preserve the previously requested mode.
    """
    with transaction():
        board = Board.get_or_none(Board.id == board_id)
        if board is None:
            return None
        board.status = BoardStatus.queued.value
        board.last_error = None
        if force_full is not None:
            board.force_full_sync = force_full
        board.updated_at = now()
        board.save()
        return board


def mark_unqueueable(board_id: int, message: str) -> Board | None:
    """Redis is down: park the board so the UI can offer a retry."""
    with transaction():
        board = Board.get_or_none(Board.id == board_id)
        if board is None:
            return None
        board.status = BoardStatus.pending.value
        board.last_error = message
        board.updated_at = now()
        board.save()
        return board


def toggle_auto_resync(board_id: int) -> Board | None:
    with transaction():
        board = Board.get_or_none(Board.id == board_id)
        if board is None:
            return None
        board.auto_resync = not board.auto_resync
        board.updated_at = now()
        board.save()
        return board


def delete_board(board_id: int) -> bool:
    """Remove a board, its pins, its media folder, and its tombstones.

    Deleting a board is meant to leave nothing behind, so the duplicate
    tombstones for its folder go too — otherwise re-adding the same board would
    silently skip pins the user deleted in a previous life.
    """
    storage = get_storage()
    with transaction():
        board = Board.get_or_none(Board.id == board_id)
        if board is None:
            return False
        folder = None
        if board.dest_path:
            folder = board.dest_path.replace("\\", "/").rstrip("/").rsplit("/", 1)[-1]
        elif board.slug:
            folder = board_folder(board.slug, board_id)
        if folder:
            storage.delete_prefix(folder)
            DeletedPin.delete().where(DeletedPin.rel_path.startswith(f"{folder}/")).execute()
        board.delete_instance(recursive=True)
    return True


def boards_for_resync() -> list[int]:
    """Ids of idle, opted-in boards for the scheduled re-sync."""
    with connection():
        return [
            b.id
            for b in Board.select(Board.id).where(
                (Board.auto_resync == True)  # noqa: E712 — SQL boolean
                & (Board.status.in_([s.value for s in _RESYNCABLE]))
            )
        ]


def interrupted_board_ids() -> list[int]:
    """Boards left mid-flight by a worker restart/crash."""
    with connection():
        return [
            b.id
            for b in Board.select(Board.id).where(
                Board.status.in_([s.value for s in _INTERRUPTED])
            )
        ]


# --------------------------------------------------------------------------- #
# sync checkpoints
# --------------------------------------------------------------------------- #
def decode_checkpoint(raw: str | None) -> list[str]:
    """Load a board checkpoint defensively; malformed legacy data is ignored."""
    if not raw:
        return []
    try:
        values = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []
    if not isinstance(values, list):
        return []
    return merge_checkpoint(values, [])


def merge_checkpoint(current, previous) -> list[str]:
    """Newest-first unique ids, capped at the ten-pin safety window."""
    merged: list[str] = []
    for values in (current, previous):
        for value in values:
            pin_id = str(value).strip() if value is not None else ""
            if pin_id and pin_id not in merged:
                merged.append(pin_id)
                if len(merged) >= SYNC_CHECKPOINT_SIZE:
                    return merged
    return merged


def encode_checkpoint(ids: list[str]) -> str:
    return json.dumps(ids, separators=(",", ":"))


def checkpoint_from_existing_pins(board_id: int) -> list[str]:
    """Seed upgraded boards without one last unavoidable full network scan.

    Pinterest ids are embedded in stable filenames and increase over time, which
    is also how the UI implements newest-first ordering. Existing sidecar ids
    therefore provide a safe initial ten-pin checkpoint after migration.
    """
    with connection():
        ids = [
            p.pinterest_id
            for p in Pin.select(Pin.pinterest_id)
            .where((Pin.board == board_id) & (Pin.pinterest_id.is_null(False)))
            .order_by(Pin.filename.desc())
        ]
    return merge_checkpoint(ids, [])
