"""arq worker: background download jobs + scheduled credential refresh."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlparse

from arq import cron
from arq.connections import RedisSettings
from sqlmodel import select

from app import appsettings, auth
from app.config import get_settings
from app.db import init_db, session_scope
from app.downloader import Progress, run_download, scan_media
from app.models import (
    Board,
    BoardStatus,
    Credential,
    CredentialStatus,
    DeletedPin,
    Pin,
)

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


def _remove_media_file(rel_path: str) -> None:
    """Delete a media file (and its sidecar) under boards_dir, guarded to it."""
    try:
        f = (settings.boards_dir / rel_path).resolve()
        if settings.boards_dir.resolve() not in f.parents:
            return
        f.unlink(missing_ok=True)
        f.with_suffix(f.suffix + ".json").unlink(missing_ok=True)
    except OSError:
        pass


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
    # Pins the user deleted as duplicates. Pinterest may still offer them (often a
    # 403 on the blocked original); run_download skips those silently instead of
    # erroring, and the reconcile below drops any that did slip through.
    with session_scope() as s:
        blocked = {
            r for r in s.exec(select(DeletedPin.rel_path)).all()
            if r.startswith(folder + "/")
        }
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
            blocked=blocked,
        ),
    )

    with session_scope() as s:
        board = s.get(Board, board_id)
        if board is None:
            return {"error": "board vanished mid-download"}

        board.downloaded_count = result.downloaded
        board.skipped_count = result.skipped
        board.error_count = result.errors
        board.dest_path = str(result.dest)
        board.log_tail = result.log_tail
        if result.board_name:
            board.title = result.board_name  # real Pinterest name (e.g. Korean)
        board.finished_at = _now()
        board.updated_at = _now()

        if result.ok or result.partial:
            # A run that produced media is "done" — recovered 403s / skipped
            # deleted pins are not a user-facing failure. Only a run that got
            # nothing is an error.
            board.status = BoardStatus.done
            board.last_error = None
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
        # Paths the user deleted as duplicates: if a re-sync re-fetched one (e.g.
        # it wasn't in the gallery-dl archive), drop the file again and skip it so
        # deleted duplicates never reappear. Only the exact deleted path is
        # blocked, so the kept copy is unaffected.
        blocked = set(s.exec(select(DeletedPin.rel_path)).all())
        seen: set[str] = set()
        for m in result.media:
            rel = f"{folder}/{m.rel_path}"
            if rel in blocked:
                _remove_media_file(rel)
                continue
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

        # Reconcile the board's counters with reality. `seen` is the true set of
        # media on disk (after blocked/deleted pins were dropped), so pin_count is
        # exactly that. gallery-dl's raw download tally counts re-downloads and
        # fallbacks that don't persist, so clamp it so "downloaded" never exceeds
        # what's actually archived.
        board.pin_count = len(seen)
        board.downloaded_count = min(board.downloaded_count, len(seen))

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
# duplicate detection (precomputed + stored, not recomputed per page view)
# --------------------------------------------------------------------------- #
async def recompute_duplicates(ctx: dict) -> dict:
    """Hash any image pins missing/stale hashes, cluster them, and persist each
    pin's dup_group (NULL when unique). The Duplicates page just reads the
    stored groups. Runs in the worker (executor) so a big archive doesn't block."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, _recompute_duplicates_blocking)


def _hash_pin(args: tuple) -> tuple:
    """Picklable pool worker: (pin_id, abs_path, is_image) -> hashes. Videos get
    an exact sha256 only (no perceptual phash), so identical video files still
    dedupe by exact bytes."""
    from app import dedup

    pid, abs_path, is_image = args
    h = dedup.compute(Path(abs_path), is_image=is_image)
    return pid, h.sha256, h.phash, h.size


def set_dedup_status(**st) -> None:
    """Persist a small JSON progress blob the Duplicates page polls (live status)."""
    try:
        appsettings.set_raw("_dedup_status", json.dumps(st))
    except Exception:  # noqa: BLE001 — status is best-effort, never fail the job
        pass


def _recompute_duplicates_blocking() -> dict:
    from concurrent.futures import ProcessPoolExecutor

    from app import dedup

    # 1) figure out which pins need (re)hashing, then hash the files in parallel
    #    across all cores (image decode + hashing is CPU-bound and independent).
    todo: list[tuple] = []
    with session_scope() as s:
        for p in s.exec(select(Pin)).all():
            is_image = p.media_type == "image"
            if is_image:
                # up to date only with both an exact + a valid perceptual hash
                if p.content_sha256 and p.phash and len(p.phash) == dedup.PHASH_HEX_LEN:
                    continue
            elif p.content_sha256:
                continue  # videos need only the exact sha256
            f = settings.boards_dir / p.rel_path
            if f.exists():
                todo.append((p.id, str(f), is_image))

    hashed = 0
    if todo:
        set_dedup_status(running=True, phase="hashing", cur=0, total=len(todo))
        workers = min(len(todo), (os.cpu_count() or 2))
        results = []
        done = 0
        last = 0.0
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(_hash_pin, todo, chunksize=16):
                results.append(r)
                done += 1
                now = time.monotonic()
                if now - last >= 0.3:
                    last = now
                    set_dedup_status(
                        running=True, phase="hashing", cur=done, total=len(todo)
                    )
        with session_scope() as s:
            for pid, sha, ph, size in results:
                p = s.get(Pin, pid)
                if p is None:
                    continue
                p.content_sha256 = sha
                p.phash = ph
                p.file_size = size
                hashed += 1

    # 2) cluster and 3) write dup_group (group id = min pin id in the cluster)
    set_dedup_status(running=True, phase="grouping")
    with session_scope() as s:
        rows = s.exec(select(Pin)).all()  # images + videos; videos group by sha
        items = [
            {"id": p.id, "content_sha256": p.content_sha256, "phash": p.phash,
             "width": p.width, "height": p.height}
            for p in rows
        ]
        groups = dedup.group_duplicates(items)
        dup_of: dict[int, int] = {}
        for g in groups:
            gid = min(it["id"] for it in g)
            for it in g:
                dup_of[it["id"]] = gid
        changed = 0
        for p in rows:
            new = dup_of.get(p.id)
            if p.dup_group != new:
                p.dup_group = new
                changed += 1
    removable = len(dup_of) - len(groups)  # extra copies beyond one-per-group
    set_dedup_status(
        running=False, phase="done", groups=len(groups),
        removable=removable, at=_now().isoformat(),
    )
    logger.info(
        "↻ dedup: hashed %s, %s group(s), %s pin(s) updated",
        hashed, len(groups), changed,
    )
    return {"hashed": hashed, "groups": len(groups)}


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


# Each schedule is a runtime-editable crontab expression (app.appsettings). A
# single job ticks every minute and runs whichever schedules are due right now,
# so editing an expression on the Settings page takes effect without a restart.
_CRON_SCHEDULES = (
    ("_last_refresh_at", "refresh_cron", refresh_all_credentials),
    ("_last_resync_at", "resync_cron", resync_all_boards),
    ("_last_dedup_at", "dedup_cron", recompute_duplicates),
)


async def _cron_dispatch(ctx: dict) -> dict:
    now = _now()
    ran: dict = {}
    for last_key, cron_key, runner in _CRON_SCHEDULES:
        expr = appsettings.get(cron_key)
        if not appsettings.cron_due(last_key, expr, now):
            continue
        appsettings.set_raw(last_key, now.isoformat())
        try:
            ran[cron_key] = await runner(ctx)
        except Exception as exc:  # noqa: BLE001 — one job must not stop the others
            logger.warning("cron %s failed: %s", cron_key, exc)
            ran[cron_key] = {"error": str(exc)}
    return ran or {"skipped": "nothing due"}


def _build_cron_jobs() -> list:
    # Fire every minute (at :00s); _cron_dispatch decides what's due.
    return [cron(_cron_dispatch, second=0)]


class WorkerSettings:
    functions = [
        download_board, refresh_credential, recompute_duplicates, resync_all_boards,
    ]
    cron_jobs = _build_cron_jobs()
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.max_concurrency
    on_startup = _startup
    keep_result = 3600
    # No board-level timeout — a big board takes as long as it needs. Stalls are
    # handled per-pin via downloader.http.timeout (settings.pin_stall_timeout).
    # (arq requires an int; a week is effectively unlimited.)
    job_timeout = 7 * 24 * 3600
