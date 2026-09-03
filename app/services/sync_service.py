"""Board synchronisation: run gallery-dl and reconcile the result into the DB.

This is the business half of the arq ``download_board`` job. It is deliberately
synchronous and free of arq/HTTP types so it can be unit-tested directly.
"""

from __future__ import annotations

import logging
import time

from app.core.config import get_settings
from app.core.db import connection, transaction
from app.models import Board, BoardStatus, DeletedPin, Pin
from app.services import board_service, credential_service, settings_service
from app.services.board_service import board_folder, derive_slug, now
from app.services.downloader import Progress, run_download, scan_media
from app.services.storage import get_storage

settings = get_settings()
logger = logging.getLogger("pinchive.download")


class SyncPlan:
    """Everything the download needs, resolved in one short transaction."""

    def __init__(self, board: Board, *, force_full: bool):
        self.board_id = board.id
        self.url = board.url
        self.slug = board.slug or derive_slug(board.url)
        self.credential_id = board.credential_id
        self.full_sync = bool(force_full or board.force_full_sync)
        self.checkpoint: list[str] = []
        self.folder = board_folder(self.slug, board.id)

    @property
    def stop_pin_ids(self) -> set[str]:
        return set() if self.full_sync else set(self.checkpoint)

    @property
    def mode(self) -> str:
        return "full" if self.full_sync or not self.checkpoint else "incremental"


def start_sync(board_id: int, *, force_full: bool = False) -> SyncPlan | None:
    """Mark the board downloading and resolve its checkpoint window."""
    with transaction():
        board = Board.get_or_none(Board.id == board_id)
        if board is None:
            return None
        board.status = BoardStatus.downloading.value
        board.started_at = now()
        board.updated_at = now()
        board.last_error = None
        if not board.slug:
            board.slug = derive_slug(board.url)
        plan = SyncPlan(board, force_full=force_full)
        checkpoint = board_service.decode_checkpoint(board.sync_checkpoint)
        if not checkpoint:
            checkpoint = board_service.checkpoint_from_existing_pins(board_id)
            if checkpoint:
                board.sync_checkpoint = board_service.encode_checkpoint(checkpoint)
        plan.checkpoint = checkpoint
        board.save()
        return plan


def blocked_paths(folder: str | None = None) -> set[str]:
    """Tombstoned (deleted-duplicate) paths, optionally limited to one board."""
    with connection():
        query = DeletedPin.select(DeletedPin.rel_path)
        if folder:
            query = query.where(DeletedPin.rel_path.startswith(f"{folder}/"))
        return {row.rel_path for row in query}


def record_progress(board_id: int, p: Progress) -> None:
    with transaction():
        Board.update(
            downloaded_count=p.downloaded,
            skipped_count=p.skipped,
            error_count=p.errors,
            updated_at=now(),
        ).where(Board.id == board_id).execute()


def sync_partial_pins(board_id: int, folder: str, dest) -> None:
    """Insert Pin rows for media already on disk (light scan, no hashes) so the
    board detail can show downloaded images while the job is still running."""
    items = scan_media(dest, with_hashes=False, with_sidecar=False)
    if not items:
        return
    with transaction():
        existing = {
            row.rel_path
            for row in Pin.select(Pin.rel_path).where(Pin.board == board_id)
        }
        new_rows = [
            {
                "board": board_id,
                "rel_path": f"{folder}/{m.rel_path}",
                "filename": m.filename,
                "media_type": m.media_type,
                "created_at": now(),
            }
            for m in items
            if f"{folder}/{m.rel_path}" not in existing
        ]
        if new_rows:
            Pin.insert_many(new_rows).execute()
        # Keep the on-disk pin count live so the UI reflects it mid-download.
        Board.update(pin_count=len(items)).where(Board.id == board_id).execute()


def finish_sync(plan: SyncPlan, result) -> str:
    """Reconcile a finished gallery-dl run into the DB. Returns the outcome."""
    storage = get_storage()
    folder = plan.folder
    with transaction():
        board = Board.get_or_none(Board.id == plan.board_id)
        if board is None:
            return "vanished"

        board.downloaded_count = result.downloaded
        board.skipped_count = result.skipped
        board.error_count = result.errors
        board.dest_path = str(result.dest)
        board.log_tail = result.log_tail
        if result.board_name:
            board.title = result.board_name  # real Pinterest name (e.g. Korean)
        board.finished_at = now()
        board.updated_at = now()
        # The persisted flag exists only so a worker/container crash resumes the
        # same forced scan. Once gallery-dl returns, even with an error, the next
        # scheduled/manual sync goes back to the safe incremental mode.
        board.force_full_sync = False

        # Only a run that actually reached a stop point (or ran to completion)
        # may move the checkpoint; otherwise an interrupted run would advance it
        # past pins it never saw and they would be missed forever.
        if result.seen_pin_ids and (
            result.stopped_at_checkpoint or result.returncode == 0
        ):
            if plan.stop_pin_ids and result.stopped_at_checkpoint:
                # Prefix new ids, then retain enough of the old window to keep
                # ten independent stop points for the next incremental sync.
                next_checkpoint = board_service.merge_checkpoint(
                    result.seen_pin_ids, plan.checkpoint
                )
            else:
                # Initial/forced scan, or every old checkpoint disappeared: the
                # emitted first ten ids are the authoritative new window.
                next_checkpoint = board_service.merge_checkpoint(result.seen_pin_ids, [])
            board.sync_checkpoint = board_service.encode_checkpoint(next_checkpoint)

        if result.ok or result.partial:
            # A run that produced media is "done" — recovered 403s / skipped
            # deleted pins are not a user-facing failure. Only a run that got
            # nothing is an error.
            board.status = BoardStatus.done.value
            board.last_error = None
        else:
            board.status = BoardStatus.error.value
            board.last_error = last_error_line(result.log_tail) or "download failed"

        # Re-sync Pin rows with what is on disk, keyed by rel_path so a pin keeps
        # its identity (and any user tags) across re-syncs instead of being
        # deleted and recreated.
        existing = {
            p.rel_path: p for p in Pin.select().where(Pin.board == plan.board_id)
        }
        # Paths the user deleted as duplicates: if a re-sync re-fetched one (e.g.
        # it was not in the gallery-dl archive), drop the file again and skip it so
        # deleted duplicates never reappear. Only the exact deleted path is
        # blocked, so the kept copy is unaffected.
        blocked = blocked_paths()
        seen: set[str] = set()
        for m in result.media:
            rel = f"{folder}/{m.rel_path}"
            if rel in blocked:
                storage.delete(rel)
                continue
            seen.add(rel)
            fields = {
                "pinterest_id": m.pinterest_id,
                "filename": m.filename,
                "media_type": m.media_type,
                "width": m.width,
                "height": m.height,
                "source_url": m.source_url,
                "title": m.title,
                "description": m.description,
            }
            # Incremental scans hash only files downloaded in this run. Preserve
            # cached hashes for archived files that were merely listed on disk.
            if m.content_sha256 is not None:
                fields["content_sha256"] = m.content_sha256
                fields["phash"] = m.phash
                fields["file_size"] = m.file_size
            pin = existing.get(rel)
            if pin is None:
                Pin.create(board=plan.board_id, rel_path=rel, **fields)
            else:
                Pin.update(**fields).where(Pin.id == pin.id).execute()
        stale = [p.id for rel, p in existing.items() if rel not in seen]
        if stale:
            Pin.delete().where(Pin.id.in_(stale)).execute()

        # Reconcile the counters with reality. `seen` is the true set of media on
        # disk (after blocked/deleted pins were dropped), so pin_count is exactly
        # that. gallery-dl's raw download tally counts re-downloads and fallbacks
        # that do not persist, so clamp it so "downloaded" never exceeds what is
        # actually archived.
        board.pin_count = len(seen)
        board.downloaded_count = min(board.downloaded_count, len(seen))
        board.save()

    return "done" if result.ok else ("partial" if result.partial else "error")


def last_error_line(log_tail: str | None) -> str | None:
    if not log_tail:
        return None
    for line in reversed(log_tail.splitlines()):
        if "[error]" in line.lower():
            return line[:300]
    return None


def run_board_sync(board_id: int, *, force_full: bool = False) -> dict:
    """Blocking end-to-end board sync. Called by the worker in an executor."""
    plan = start_sync(board_id, force_full=force_full)
    if plan is None:
        return {"error": "board not found"}

    dest = settings.boards_dir / plan.folder
    # Per-board archive: each board stays a faithful mirror of its Pinterest
    # contents (a pin shared across boards downloads into each), while re-syncing
    # a board still skips pins it already has. Cross-board duplicate *images* are
    # surfaced by the duplicates view, not silently dropped here.
    archive_file = dest / ".gallery-dl-archive.db"
    cookies_file = None
    if plan.credential_id is not None:
        candidate = credential_service.cookies_path(plan.credential_id)
        cookies_file = candidate if candidate.exists() else None

    logger.info("board %s: %s sync %s", board_id, plan.mode, plan.url)
    blocked = blocked_paths(plan.folder)
    last_pin_sync = [0.0]

    def on_progress(p: Progress) -> None:
        record_progress(board_id, p)
        # Surface partial results so images show while the board is still
        # downloading (throttled — a light scan, no hashing yet).
        tick = time.monotonic()
        if tick - last_pin_sync[0] >= 2.0:
            last_pin_sync[0] = tick
            sync_partial_pins(board_id, plan.folder, dest)

    result = run_download(
        plan.url,
        dest,
        cookies_file=cookies_file,
        archive_file=archive_file,
        sleep=settings_service.get("dl_sleep"),
        stall_timeout=settings_service.get("pin_stall_timeout"),
        on_progress=on_progress,
        blocked=blocked,
        stop_pin_ids=plan.stop_pin_ids,
    )

    status = finish_sync(plan, result)
    if status == "done":
        logger.info(
            "board %s done: %s downloaded, %s skipped",
            board_id, result.downloaded, result.skipped,
        )
    elif status == "partial":
        logger.warning(
            "board %s partial: %s downloaded, %s skipped, %s errors",
            board_id, result.downloaded, result.skipped, result.errors,
        )
    elif status == "error":
        logger.error(
            "board %s failed: %s", board_id,
            last_error_line(result.log_tail) or "download failed",
        )

    return {
        "downloaded": result.downloaded,
        "skipped": result.skipped,
        "errors": result.errors,
        "status": status,
        "mode": plan.mode,
        "stopped_at_checkpoint": result.stopped_at_checkpoint,
    }
