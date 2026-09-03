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
from pathlib import Path

from app.core.db import connection, transaction
from app.models import Pin
from app.services import dedup, settings_service
from app.services.board_service import Page, now
from app.services.storage import get_storage

logger = logging.getLogger("pinchive.dedup")

_STATUS_KEY = "_dedup_status"


def set_status(**st) -> None:
    """Persist a small JSON progress blob the UI polls (live status)."""
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
    return parsed if isinstance(parsed, dict) else {}


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


def _pins_needing_hashes() -> list[tuple]:
    storage = get_storage()
    todo: list[tuple] = []
    with connection():
        for p in Pin.select():
            is_image = p.media_type == "image"
            if is_image:
                # Up to date only with both an exact and a valid perceptual hash.
                if p.content_sha256 and p.phash and len(p.phash) == dedup.PHASH_HEX_LEN:
                    continue
            elif p.content_sha256:
                continue  # videos need only the exact sha256
            path = storage.local_path(p.rel_path)
            if path is not None and path.exists():
                todo.append((p.id, str(path), is_image))
    return todo


def recompute() -> dict:
    """Hash pins missing/stale hashes, cluster them, persist each dup_group."""
    from concurrent.futures import ProcessPoolExecutor

    todo = _pins_needing_hashes()
    hashed = 0
    if todo:
        set_status(running=True, phase="hashing", cur=0, total=len(todo))
        workers = min(len(todo), (os.cpu_count() or 2))
        results = []
        done = 0
        last = 0.0
        with ProcessPoolExecutor(max_workers=workers) as ex:
            for r in ex.map(_hash_pin, todo, chunksize=16):
                results.append(r)
                done += 1
                tick = time.monotonic()
                if tick - last >= 0.3:
                    last = tick
                    set_status(
                        running=True, phase="hashing", cur=done, total=len(todo)
                    )
        with transaction():
            for pid, sha, ph, size in results:
                updated = (
                    Pin.update(content_sha256=sha, phash=ph, file_size=size)
                    .where(Pin.id == pid)
                    .execute()
                )
                hashed += updated

    set_status(running=True, phase="grouping")
    with transaction():
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
        for g in groups:
            gid = min(it["id"] for it in g)
            for it in g:
                dup_of[it["id"]] = gid
        changed = 0
        for p in rows:
            new = dup_of.get(p.id)
            if p.dup_group != new:
                Pin.update(dup_group=new).where(Pin.id == p.id).execute()
                changed += 1

    removable = len(dup_of) - len(groups)  # extra copies beyond one-per-group
    set_status(
        running=False, phase="done", groups=len(groups),
        removable=removable, at=now().isoformat(),
    )
    logger.info(
        "dedup: hashed %s, %s group(s), %s pin(s) updated", hashed, len(groups), changed
    )
    return {"hashed": hashed, "groups": len(groups), "removable": removable}
