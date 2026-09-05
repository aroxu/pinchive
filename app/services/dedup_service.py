"""Duplicate detection orchestration.

Hashing is CPU-bound, so a rescan hashes across all cores, clusters the result
with :mod:`app.services.dedup`, and stores each pin's ``dup_group``. The API
only ever reads the stored groups — never recomputes per request.
"""

from __future__ import annotations

import json
import logging
import os
import time
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
from datetime import datetime, timedelta
from pathlib import Path

from app.core.db import connection, transaction
from app.models import Pin
from app.services import dedup, settings_service
from app.services.board_service import Page, now
from app.services.storage import get_storage

logger = logging.getLogger("pinchive.dedup")

_STATUS_KEY = "_dedup_status"
_ACTIVE_PHASES = {"preparing", "hashing", "saving", "grouping"}
_STALE_AFTER = timedelta(minutes=5)
_WRITE_BATCH_SIZE = 256


def set_status(**st) -> None:
    """Persist a small JSON progress blob the UI polls (live status)."""
    st["updated_at"] = now().isoformat()
    try:
        settings_service.set_raw(_STATUS_KEY, json.dumps(st))
    except Exception:  # noqa: BLE001 — status is best-effort, never fail the job
        pass


def get_status() -> dict:
    raw = settings_service.get_raw(_STATUS_KEY)
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}

    # A worker can be killed without reaching the exception handler. Active
    # phases emit a heartbeat, so an old timestamp means the calculation is no
    # longer making progress. Clear the sticky running flag instead of making
    # the UI say "Scanning…" forever. Queued jobs are exempt because they can
    # legitimately wait behind a long board download.
    if parsed.get("running") and parsed.get("phase") in _ACTIVE_PHASES:
        stamp = parsed.get("updated_at")
        try:
            updated = datetime.fromisoformat(stamp) if stamp else None
        except (TypeError, ValueError):
            updated = None
        current = now()
        if updated is not None and updated.tzinfo is None:
            updated = updated.replace(tzinfo=current.tzinfo)
        if updated is None or current - updated > _STALE_AFTER:
            parsed.update(
                running=False,
                phase="failed",
                error="Duplicate scan stopped before completion.",
                updated_at=current.isoformat(),
            )
            try:
                settings_service.set_raw(_STATUS_KEY, json.dumps(parsed))
            except Exception:  # noqa: BLE001 — recovery remains best-effort
                pass
    return parsed


def recover_interrupted_status() -> bool:
    """Clear a persisted in-progress state when a worker starts.

    No duplicate job can have survived *inside* this new worker process. A
    queued Redis job may still start later and will publish a fresh status.
    """
    status = get_status()
    if not status.get("running"):
        return False
    set_status(
        running=False,
        phase="failed",
        error="Duplicate scan was interrupted by a worker restart.",
    )
    return True


def stored_groups() -> list[list[Pin]]:
    """Pins grouped by their precomputed dup_group, highest-res copy first."""
    with connection():
        rows = list(
            Pin.select().where(Pin.dup_group.is_null(False)).order_by(Pin.dup_group)
        )
    by_group: dict[int, list[Pin]] = {}
    for p in rows:
        by_group.setdefault(p.dup_group, []).append(p)
    groups = [g for g in by_group.values() if len(g) >= 2]
    for g in groups:
        g.sort(key=lambda p: (p.width or 0) * (p.height or 0), reverse=True)
    groups.sort(key=len, reverse=True)
    return groups


def duplicate_page(page: int, per_page: int) -> tuple[Page, int]:
    """One page of duplicate groups plus the archive-wide removable count."""
    groups = stored_groups()
    total_extra = sum(len(g) - 1 for g in groups)
    total = len(groups)
    pages = max(1, -(-total // per_page)) if total else 1
    page = min(max(1, page), pages)
    start = (page - 1) * per_page
    return Page(groups[start:start + per_page], page, pages, total), total_extra


def redundant_pin_ids() -> list[int]:
    """Every copy except the highest-resolution keeper in each group."""
    return [p.id for group in stored_groups() for p in group[1:]]


# --------------------------------------------------------------------------- #
# recompute
# --------------------------------------------------------------------------- #
def _hash_pin(args: tuple) -> tuple:
    """Picklable pool worker: (pin_id, abs_path, is_image) -> hashes. Videos get
    an exact sha256 only (no perceptual phash), so identical video files still
    dedupe by exact bytes."""
    pid, abs_path, is_image = args
    h = dedup.compute(Path(abs_path), is_image=is_image)
    return pid, h.sha256, h.phash, h.size


def _hash_batch(batch: list[tuple]) -> list[tuple]:
    """Hash one small batch in a child process.

    Futures complete out of order, so one slow file no longer blocks progress
    from every faster batch behind it.
    """
    return [_hash_pin(item) for item in batch]


def _pins_needing_hashes() -> list[tuple]:
    storage = get_storage()
    todo: list[tuple] = []
    with connection():
        for p in Pin.select():
            is_image = p.media_type == "image"
            if is_image:
                # SHA present + no phash means Pillow already tried and found
                # no useful visual hash (flat/corrupt image). Do not re-read it
                # on every scheduled scan. A non-empty legacy hash is refreshed
                # when its length differs from the current 256-bit format.
                if p.content_sha256 and (
                    not p.phash or len(p.phash) == dedup.PHASH_HEX_LEN
                ):
                    continue
            elif p.content_sha256:
                continue  # videos need only the exact sha256
            path = storage.local_path(p.rel_path)
            if path is not None and path.exists():
                todo.append((p.id, str(path), is_image))
    return todo


def recompute() -> dict:
    """Hash pins missing/stale hashes, cluster them, persist each dup_group."""
    set_status(running=True, phase="preparing")
    try:
        todo = _pins_needing_hashes()
        hashed = 0
        results: list[tuple] = []
        if todo:
            set_status(running=True, phase="hashing", cur=0, total=len(todo))
            workers = min(len(todo), (os.cpu_count() or 2))
            batches = [
                todo[i:i + 16]
                for i in range(0, len(todo), 16)
            ]
            done = 0
            last = 0.0
            with ProcessPoolExecutor(max_workers=workers) as executor:
                pending = {
                    executor.submit(_hash_batch, batch)
                    for batch in batches
                }
                while pending:
                    completed, pending = wait(
                        pending,
                        timeout=0.5,
                        return_when=FIRST_COMPLETED,
                    )
                    for future in completed:
                        batch_results = future.result()
                        results.extend(batch_results)
                        done += len(batch_results)
                    tick = time.monotonic()
                    # Keep the heartbeat fresh even when one remaining batch is
                    # hashing a very large video and the numeric count cannot
                    # advance yet.
                    if done == len(todo) or tick - last >= 0.5:
                        last = tick
                        set_status(
                            running=True, phase="hashing", cur=done, total=len(todo)
                        )

            set_status(running=True, phase="saving", cur=0, total=len(results))
            for start in range(0, len(results), _WRITE_BATCH_SIZE):
                batch = results[start:start + _WRITE_BATCH_SIZE]
                with transaction():
                    for pid, sha, ph, size in batch:
                        hashed += (
                            Pin.update(
                                content_sha256=sha,
                                phash=ph,
                                file_size=size,
                            )
                            .where(Pin.id == pid)
                            .execute()
                        )
                set_status(
                    running=True,
                    phase="saving",
                    cur=min(start + len(batch), len(results)),
                    total=len(results),
                )

        set_status(running=True, phase="grouping", cur=0, total=0)
        with connection():
            rows = list(Pin.select())  # images + videos; videos group by sha
        items = [
            {
                "id": p.id,
                "content_sha256": p.content_sha256,
                "phash": p.phash,
                "width": p.width,
                "height": p.height,
            }
            for p in rows
        ]
        groups = dedup.group_duplicates(items)
        dup_of: dict[int, int] = {}
        for group in groups:
            gid = min(item["id"] for item in group)
            for item in group:
                dup_of[item["id"]] = gid

        changed = 0
        set_status(running=True, phase="grouping", cur=0, total=len(rows))
        for start in range(0, len(rows), _WRITE_BATCH_SIZE):
            batch = rows[start:start + _WRITE_BATCH_SIZE]
            with transaction():
                for pin in batch:
                    new = dup_of.get(pin.id)
                    if pin.dup_group != new:
                        Pin.update(dup_group=new).where(Pin.id == pin.id).execute()
                        changed += 1
            set_status(
                running=True,
                phase="grouping",
                cur=min(start + len(batch), len(rows)),
                total=len(rows),
            )

        removable = len(dup_of) - len(groups)
        set_status(
            running=False,
            phase="done",
            groups=len(groups),
            removable=removable,
            at=now().isoformat(),
        )
        logger.info(
            "dedup: hashed %s, %s group(s), %s pin(s) updated",
            hashed,
            len(groups),
            changed,
        )
        return {"hashed": hashed, "groups": len(groups), "removable": removable}
    except Exception as exc:
        set_status(running=False, phase="failed", error=str(exc))
        logger.exception("duplicate scan failed")
        raise
