"""arq worker: background download jobs + scheduled credential refresh."""

from __future__ import annotations

import asyncio
import functools
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

from arq import cron
from arq.connections import RedisSettings
from sqlmodel import select

from app import auth
from app.config import get_settings
from app.db import init_db, session_scope
from app.downloader import Progress, run_download
from app.models import Board, BoardStatus, Credential, CredentialStatus, Pin

settings = get_settings()


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

    def on_progress(p: Progress) -> None:
        with session_scope() as s:
            b = s.get(Board, board_id)
            if b is None:
                return
            b.downloaded_count = p.downloaded
            b.skipped_count = p.skipped
            b.error_count = p.errors
            b.updated_at = _now()

    result = await loop.run_in_executor(
        None,
        functools.partial(
            run_download,
            url,
            dest,
            cookies_file=cookies_file,
            archive_file=archive_file,
            sleep=settings.dl_sleep,
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

    return {
        "downloaded": result.downloaded,
        "skipped": result.skipped,
        "errors": result.errors,
        "status": "done" if result.ok else ("partial" if result.partial else "error"),
    }


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
    if not res.active and settings.use_playwright_fallback:
        await _attempt_auto_refresh(cred_id)

    return {"active": res.active, "message": res.message, "rotated": res.rotated}


async def refresh_all_credentials(ctx: dict) -> dict:
    with session_scope() as s:
        ids = [c.id for c in s.exec(select(Credential)).all() if c.id is not None]
    for cid in ids:
        await refresh_credential(ctx, cid)
    return {"checked": len(ids)}


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
async def _startup(ctx: dict) -> None:
    settings.ensure_dirs()
    init_db()


class WorkerSettings:
    functions = [download_board, refresh_credential]
    cron_jobs = [
        cron(
            refresh_all_credentials,
            hour=settings.refresh_hours(),
            minute=settings.refresh_minute,
        )
    ]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.max_concurrency
    on_startup = _startup
    keep_result = 3600
