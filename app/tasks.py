"""arq worker: background download jobs + scheduled credential refresh."""

from __future__ import annotations

import asyncio
import functools
import logging
import re
import sys
import time
from datetime import datetime, timezone
from urllib.parse import urlparse

from arq import cron
from arq.connections import RedisSettings
from sqlmodel import select

from app import appsettings, auth
from app.config import get_settings
from app.db import init_db, session_scope
from app.downloader import Progress, run_download, scan_media
from app.models import Board, BoardStatus, Credential, CredentialStatus, Pin

settings = get_settings()
logger = logging.getLogger("pinchive.download")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def derive_slug(url: str) -> str:
    """`pinterest.com/<user>/<board>/` -> `user__board`; else a safe fallback."""
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


# --------------------------------------------------------------------------- #
# download job
# --------------------------------------------------------------------------- #
async def download_board(ctx: dict, board_id: int) -> dict:
    loop = asyncio.get_running_loop()

    with session_scope() as s:
        board = s.get(Board, board_id)
        if board is None:
            return {"error": "board not found"}
        board.status = BoardStatus.downloading
        board.started_at = _now()
        board.updated_at = _now()
        board.last_error = None
        if not board.slug:
            board.slug = derive_slug(board.url)
        slug = board.slug
        url = board.url
        cred_id = board.credential_id

    # Folder is suffixed with the board id so two boards that derive the same
    # slug (e.g. the same URL added twice) never share a directory and clobber
    # each other's files / archive.
    folder = board_folder(slug, board_id)
    dest = settings.boards_dir / folder
    # Per-board archive: each board stays a faithful mirror of its Pinterest
    # contents (a pin shared across boards downloads into each), while re-syncing
    # a board still skips pins it already has. Cross-board / cross-pin duplicate
    # *images* are surfaced by the Duplicates view, not silently dropped here.
    archive_file = dest / ".gallery-dl-archive.db"
    cookies_file = None
    if cred_id is not None:
        cf = auth.cookies_path(cred_id)
        cookies_file = cf if cf.exists() else None

    logger.info("▶ board %s: downloading %s", board_id, url)
    _last_pin_sync = [0.0]

    def on_progress(p: Progress) -> None:
        with session_scope() as s:
            b = s.get(Board, board_id)
            if b is None:
                return
            b.downloaded_count = p.downloaded
            b.skipped_count = p.skipped
            b.error_count = p.errors
            b.updated_at = _now()
        # Surface partial results so images show while the board is still
        # downloading (throttled — a light scan, no hashing yet).
        now = time.monotonic()
        if now - _last_pin_sync[0] >= 2.0:
            _last_pin_sync[0] = now
            _sync_partial_pins(board_id, folder, dest)

    result = await loop.run_in_executor(
        None,
        functools.partial(
            run_download,
            url,
            dest,
            cookies_file=cookies_file,
            archive_file=archive_file,
            sleep=appsettings.get("dl_sleep"),
            stall_timeout=appsettings.get("pin_stall_timeout"),
            on_progress=on_progress,
        ),
    )

    with session_scope() as s:
        board = s.get(Board, board_id)
        if board is None:
            return {"error": "board vanished mid-download"}

        board.downloaded_count = result.downloaded
        board.skipped_count = result.skipped
        board.error_count = result.errors
        board.pin_count = len(result.media)
        board.dest_path = str(result.dest)
        board.log_tail = result.log_tail
        if result.board_name:
            board.title = result.board_name  # real Pinterest name (e.g. Korean)
        board.finished_at = _now()
        board.updated_at = _now()

        if result.ok:
            board.status = BoardStatus.done
        elif result.partial:
            board.status = BoardStatus.partial
            board.last_error = "some pins failed — see log"
        else:
            board.status = BoardStatus.error
            board.last_error = _last_error_line(result.log_tail) or "download failed"

        # Re-sync Pin rows with what is on disk, keyed by rel_path so a pin keeps
        # its identity (and any user tags) across re-syncs instead of being
        # deleted and recreated.
        existing = {
            p.rel_path: p
            for p in s.exec(select(Pin).where(Pin.board_id == board_id)).all()
        }
        seen: set[str] = set()
        for m in result.media:
            rel = f"{folder}/{m.rel_path}"
            seen.add(rel)
            pin = existing.get(rel) or Pin(board_id=board_id, rel_path=rel)
            pin.pinterest_id = m.pinterest_id
            pin.filename = m.filename
            pin.media_type = m.media_type
            pin.width = m.width
            pin.height = m.height
            pin.source_url = m.source_url
            pin.title = m.title
            pin.description = m.description
            pin.content_sha256 = m.content_sha256
            pin.phash = m.phash
            pin.file_size = m.file_size
            s.add(pin)
        for rel, pin in existing.items():
            if rel not in seen:
                s.delete(pin)

    status = "done" if result.ok else ("partial" if result.partial else "error")
    if result.ok:
        logger.info(
            "✔ board %s done: %s downloaded, %s skipped",
            board_id, result.downloaded, result.skipped,
        )
    elif result.partial:
        logger.warning(
            "◐ board %s partial: %s downloaded, %s skipped, %s errors — see log",
            board_id, result.downloaded, result.skipped, result.errors,
        )
    else:
        logger.error(
            "x board %s failed: %s", board_id,
            _last_error_line(result.log_tail) or "download failed",
        )

    return {
        "downloaded": result.downloaded,
        "skipped": result.skipped,
        "errors": result.errors,
        "status": status,
    }


def _sync_partial_pins(board_id: int, folder: str, dest) -> None:
    """Insert Pin rows for media already on disk (light scan, no hashes) so the
    board detail can show downloaded images while the job is still running."""
    items = scan_media(dest, with_hashes=False, with_sidecar=False)
    if not items:
        return
    with session_scope() as s:
        existing = set(
            s.exec(select(Pin.rel_path).where(Pin.board_id == board_id)).all()
        )
        for m in items:
            rel = f"{folder}/{m.rel_path}"
            if rel not in existing:
                s.add(Pin(
                    board_id=board_id, rel_path=rel,
                    filename=m.filename, media_type=m.media_type,
                ))


def _last_error_line(log_tail: str | None) -> str | None:
    if not log_tail:
        return None
    for line in reversed(log_tail.splitlines()):
        if "[error]" in line.lower():
            return line[:300]
    return None


# --------------------------------------------------------------------------- #
# credential refresh
# --------------------------------------------------------------------------- #
async def refresh_credential(ctx: dict, cred_id: int) -> dict:
    """Keep-alive one credential: authenticated request + persist rotated cookies."""
    loop = asyncio.get_running_loop()
    path = auth.cookies_path(cred_id)
    res = await loop.run_in_executor(
        None, functools.partial(auth.refresh_session, path)
    )
    with session_scope() as s:
        cred = s.get(Credential, cred_id)
        if cred is None:
            return {"error": "credential not found"}
        cred.status = CredentialStatus.active if res.active else CredentialStatus.expired
        cred.last_checked_at = _now()
        cred.last_error = None if res.active else res.message
        cred.updated_at = _now()

    # Session truly dead (server-side logout / long inactivity). Cookie rotation
    # can't help here — only a full re-login can, if configured.
    if not res.active and appsettings.get("use_playwright_fallback"):
        await _attempt_auto_refresh(cred_id)

    return {"active": res.active, "message": res.message, "rotated": res.rotated}


async def refresh_all_credentials(ctx: dict) -> dict:
    with session_scope() as s:
        ids = [c.id for c in s.exec(select(Credential)).all() if c.id is not None]
    for cid in ids:
        await refresh_credential(ctx, cid)
    return {"checked": len(ids)}


# --------------------------------------------------------------------------- #
# manual duplicate rescan
# --------------------------------------------------------------------------- #
async def rescan_hashes(ctx: dict) -> dict:
    """Compute content_sha256 + phash for image pins that lack them (e.g. pins
    created by the mid-download light scan), so the Duplicates view catches
    them. Runs in the worker so a big archive doesn't block the web request."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _rescan_hashes_blocking)


def _rescan_hashes_blocking() -> dict:
    from app import dedup

    with session_scope() as s:
        pins = s.exec(
            select(Pin).where(
                Pin.media_type == "image", Pin.content_sha256.is_(None)
            )
        ).all()
        ids = [p.id for p in pins]
    updated = 0
    for pid in ids:
        with session_scope() as s:
            p = s.get(Pin, pid)
            if p is None:
                continue
            f = settings.boards_dir / p.rel_path
            if not f.exists():
                continue
            h = dedup.compute(f, is_image=True)
            p.content_sha256 = h.sha256
            p.phash = h.phash
            p.file_size = h.size
            updated += 1
    logger.info("↻ duplicate rescan: hashed %s pin(s)", updated)
    return {"hashed": updated}


# --------------------------------------------------------------------------- #
# board auto-resync
# --------------------------------------------------------------------------- #
_RESYNCABLE = (BoardStatus.done, BoardStatus.partial, BoardStatus.error)


async def resync_all_boards(ctx: dict) -> dict:
    """Cron: enqueue a re-download for every opted-in board that's idle.

    The per-board --download-archive makes this cheap: only new pins are
    actually fetched. Boards mid-flight (queued/downloading) are skipped so we
    never double-queue.
    """
    with session_scope() as s:
        ids = [
            b.id
            for b in s.exec(
                select(Board).where(
                    Board.auto_resync == True,  # noqa: E712 (SQL boolean)
                    Board.status.in_(_RESYNCABLE),
                )
            ).all()
            if b.id is not None
        ]

    pool = ctx.get("redis")
    if pool is None:
        return {"enqueued": 0, "error": "no queue"}
    for bid in ids:
        with session_scope() as s:
            b = s.get(Board, bid)
            if b is not None:
                b.status = BoardStatus.queued
                b.updated_at = _now()
        await pool.enqueue_job("download_board", bid)
    return {"enqueued": len(ids)}


async def _attempt_auto_refresh(cred_id: int) -> None:
    """Optional Playwright re-login. No-op unless the extra is installed and a
    stored account/password profile exists. Intentionally soft-failing."""
    try:
        from app.refresh_browser import relogin  # type: ignore
    except ImportError:
        return
    try:
        await relogin(cred_id)
    except Exception:  # noqa: BLE001 — never let refresh crash the worker
        return


# --------------------------------------------------------------------------- #
# worker settings
# --------------------------------------------------------------------------- #
def configure_download_logging() -> None:
    """Send pinchive.download logs to stdout so they appear in `docker compose
    logs -f worker`. Idempotent."""
    lg = logging.getLogger("pinchive.download")
    if lg.handlers:
        return
    h = logging.StreamHandler(sys.stdout)
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
    lg.addHandler(h)
    lg.setLevel(logging.INFO)
    lg.propagate = False


_INTERRUPTED = (BoardStatus.downloading, BoardStatus.queued, BoardStatus.pending)


async def _resume_interrupted(ctx: dict) -> None:
    """Re-enqueue boards left mid-flight by a worker restart/crash. The per-board
    archive makes this a resume: already-downloaded pins are skipped, the rest
    continue."""
    pool = ctx.get("redis")
    if pool is None:
        return
    with session_scope() as s:
        ids = [
            b.id for b in s.exec(select(Board).where(Board.status.in_(_INTERRUPTED))).all()
            if b.id is not None
        ]
    for bid in ids:
        with session_scope() as s:
            b = s.get(Board, bid)
            if b is not None:
                b.status = BoardStatus.queued
                b.updated_at = _now()
        await pool.enqueue_job("download_board", bid)
    if ids:
        logger.info("↻ resuming %s interrupted board(s): %s", len(ids), ids)


async def _startup(ctx: dict) -> None:
    settings.ensure_dirs()
    init_db()
    configure_download_logging()
    await _resume_interrupted(ctx)


# The cron intervals are runtime-editable (app.appsettings), so the crons fire
# hourly and gate on the effective interval + a stored last-run timestamp. That
# makes an interval change on the Settings page take effect without a restart.
async def _cron_refresh(ctx: dict) -> dict:
    interval = appsettings.get("refresh_every_hours")
    if not interval or interval <= 0:
        return {"skipped": "disabled"}
    if not appsettings.due("_last_refresh_at", interval):
        return {"skipped": "not due"}
    appsettings.set_raw("_last_refresh_at", _now().isoformat())
    return await refresh_all_credentials(ctx)


async def _cron_resync(ctx: dict) -> dict:
    interval = appsettings.get("resync_every_hours")
    if not interval or interval <= 0:
        return {"skipped": "disabled"}
    if not appsettings.due("_last_resync_at", interval):
        return {"skipped": "not due"}
    appsettings.set_raw("_last_resync_at", _now().isoformat())
    return await resync_all_boards(ctx)


def _build_cron_jobs() -> list:
    return [
        cron(_cron_refresh, minute=0),   # hourly; gated by refresh interval
        cron(_cron_resync, minute=30),   # hourly; gated by resync interval
    ]


class WorkerSettings:
    functions = [download_board, refresh_credential, rescan_hashes, resync_all_boards]
    cron_jobs = _build_cron_jobs()
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.max_concurrency
    on_startup = _startup
    keep_result = 3600
    # No board-level timeout — a big board takes as long as it needs. Stalls are
    # handled per-pin via downloader.http.timeout (settings.pin_stall_timeout).
    # (arq requires an int; a week is effectively unlimited.)
    job_timeout = 7 * 24 * 3600
