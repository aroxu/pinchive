"""arq worker: background jobs + the crontab dispatcher.

Every job here is a thin async wrapper: blocking work is delegated to
``app.services`` inside an executor so the event loop keeps serving arq.
"""

from __future__ import annotations

import asyncio
import functools
import logging
import sys

from arq import Retry, cron
from arq.connections import RedisSettings
from peewee import OperationalError

from app.core.config import get_settings
from app.models import Board, BoardStatus, init_db
from app.services import (
    board_service,
    credential_store,
    dedup_service,
    settings_service,
    sync_service,
)
from app.services.board_service import now

settings = get_settings()
logger = logging.getLogger("pinchive.download")


async def _run(func, *args, **kwargs):
    """Run blocking service code off the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, functools.partial(func, *args, **kwargs))


def _retry_if_database_locked(ctx: dict, exc: OperationalError) -> None:
    """Turn a residual SQLite writer collision into an arq retry.

    ``BEGIN IMMEDIATE`` and the busy timeout handle normal contention. This is a
    final safety net for unusually slow storage or maintenance operations so a
    queued board is not silently abandoned.
    """
    if "database is locked" not in str(exc).lower():
        raise exc
    attempt = max(1, int(ctx.get("job_try", 1)))
    delay = min(2 ** (attempt - 1), 30)
    logger.warning("database busy; retrying job in %ss (attempt %s)", delay, attempt)
    raise Retry(defer=delay) from exc


# --------------------------------------------------------------------------- #
# jobs
# --------------------------------------------------------------------------- #
async def download_board(ctx: dict, board_id: int, force_full: bool = False) -> dict:
    try:
        return await _run(sync_service.run_board_sync, board_id, force_full=force_full)
    except OperationalError as exc:
        _retry_if_database_locked(ctx, exc)
        raise  # pragma: no cover - helper always raises


async def refresh_credential(ctx: dict, cred_id: int) -> dict:
    result = await _run(credential_store.refresh_credential, cred_id)
    return {
        "active": result.active,
        "message": result.message,
        "rotated": result.rotated,
    }


async def refresh_all_credentials(ctx: dict) -> dict:
    ids = await _run(credential_store.all_credential_ids)
    for cred_id in ids:
        await refresh_credential(ctx, cred_id)
    return {"checked": len(ids)}


async def recompute_duplicates(ctx: dict) -> dict:
    return await _run(dedup_service.recompute)


async def resync_all_boards(ctx: dict) -> dict:
    """Cron: enqueue a re-download for every opted-in board that is idle.

    Each board stops at its previous ten-pin checkpoint, and its per-board
    download archive still prevents duplicate file downloads. Boards mid-flight
    (queued/downloading) are skipped so we never double-queue.
    """
    ids = await _run(board_service.boards_for_resync)
    pool = ctx.get("redis")
    if pool is None:
        return {"enqueued": 0, "error": "no queue"}
    for board_id in ids:
        await _run(board_service.mark_queued, board_id)
        await pool.enqueue_job("download_board", board_id)
    return {"enqueued": len(ids)}


# --------------------------------------------------------------------------- #
# startup / cron
# --------------------------------------------------------------------------- #
def configure_download_logging() -> None:
    """Send pinchive logs to stdout so they appear in `docker compose logs -f
    worker`. Idempotent."""
    for name in ("pinchive.download", "pinchive.dedup"):
        lg = logging.getLogger(name)
        if lg.handlers:
            continue
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", "%H:%M:%S"))
        lg.addHandler(handler)
        lg.setLevel(logging.INFO)
        lg.propagate = False


async def _resume_interrupted(ctx: dict) -> None:
    """Re-enqueue boards left mid-flight by a worker restart/crash. The per-board
    archive makes this a resume: already-downloaded pins are skipped, the rest
    continue."""
    pool = ctx.get("redis")
    if pool is None:
        return
    ids = await _run(board_service.interrupted_board_ids)
    for board_id in ids:
        await _run(board_service.mark_queued, board_id)
        await pool.enqueue_job("download_board", board_id)
    if ids:
        logger.info("resuming %s interrupted board(s): %s", len(ids), ids)


async def _startup(ctx: dict) -> None:
    settings.ensure_dirs()
    init_db()
    configure_download_logging()
    if await _run(dedup_service.recover_interrupted_status):
        logger.warning("cleared interrupted duplicate scan status")
    await _resume_interrupted(ctx)


# Each schedule is a runtime-editable crontab expression (app.services.
# settings_service). A single job ticks every minute and runs whichever
# schedules are due, so editing an expression takes effect without a restart.
_CRON_SCHEDULES = (
    ("_last_refresh_at", "refresh_cron", refresh_all_credentials),
    ("_last_resync_at", "resync_cron", resync_all_boards),
    ("_last_dedup_at", "dedup_cron", recompute_duplicates),
)


async def _cron_dispatch(ctx: dict) -> dict:
    tick = now()
    ran: dict = {}
    for last_key, cron_key, runner in _CRON_SCHEDULES:
        expr = settings_service.get(cron_key)
        if not settings_service.cron_due(last_key, expr, tick):
            continue
        settings_service.set_raw(last_key, tick.isoformat())
        try:
            ran[cron_key] = await runner(ctx)
        except Exception as exc:  # noqa: BLE001 — one job must not stop the others
            logger.warning("cron %s failed: %s", cron_key, exc)
            ran[cron_key] = {"error": str(exc)}
    return ran or {"skipped": "nothing due"}


class WorkerSettings:
    functions = [
        download_board,
        refresh_credential,
        recompute_duplicates,
        resync_all_boards,
    ]
    # Fire every minute (at :00s); _cron_dispatch decides what is due.
    cron_jobs = [cron(_cron_dispatch, second=0)]
    redis_settings = RedisSettings.from_dsn(settings.redis_url)
    max_jobs = settings.max_concurrency
    on_startup = _startup
    keep_result = 3600
    # No board-level timeout — a big board takes as long as it needs. Stalls are
    # handled per-pin via downloader.http.timeout (settings.pin_stall_timeout).
    # (arq requires an int; a week is effectively unlimited.)
    job_timeout = 7 * 24 * 3600


__all__ = ["WorkerSettings", "Board", "BoardStatus"]
