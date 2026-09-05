"""Sync service + worker jobs: slug/folder derivation, checkpoints, crons."""

import asyncio
import json
import shutil
from datetime import UTC, datetime, timedelta

import pytest

from app.core.config import get_settings
from app.models import Board, BoardStatus, Pin
from app.services import dedup_service, settings_service, sync_service
from app.services.board_service import (
    board_folder,
    checkpoint_from_existing_pins,
    decode_checkpoint,
    derive_slug,
    merge_checkpoint,
)
from app.services.dedup_service import recompute
from app.worker import _cron_dispatch, _resume_interrupted, resync_all_boards

settings = get_settings()


# --------------------------------------------------------------------------- #
# slug / folder
# --------------------------------------------------------------------------- #
def test_derive_slug_board_url():
    assert derive_slug("https://www.pinterest.com/john/cool-board/") == "john__cool-board"


def test_derive_slug_pin_url():
    assert derive_slug("https://www.pinterest.com/pin/12345/") == "pin__12345"


def test_derive_slug_sanitizes():
    s = derive_slug("https://www.pinterest.com/a b/c!d/")
    assert " " not in s and "!" not in s


def test_derive_slug_fallback():
    assert derive_slug("https://www.pinterest.com/") == "board"


def test_same_url_boards_get_distinct_folders():
    # Regression: two boards with the same URL derive the same slug but must NOT
    # share a directory (else they clobber each other's files).
    slug = derive_slug("https://www.pinterest.com/pin/424605071112831904/")
    assert board_folder(slug, 1) != board_folder(slug, 2)
    assert board_folder(slug, 1).endswith("-1")


# --------------------------------------------------------------------------- #
# checkpoints
# --------------------------------------------------------------------------- #
def test_sync_checkpoint_merge_is_unique_and_capped():
    previous = [str(n) for n in range(20, 10, -1)]
    merged = merge_checkpoint(["22", "21", "20", "22"], previous)
    assert merged == ["22", "21", "20", "19", "18", "17", "16", "15", "14", "13"]
    assert decode_checkpoint("not-json") == []
    assert decode_checkpoint(json.dumps(["2", "2", "1"])) == ["2", "1"]


def test_checkpoint_bootstraps_from_existing_newest_pins():
    bid = _mk(BoardStatus.done, True)
    for filename, pinterest_id in (
        ("pinterest_100.jpg", "100"),
        ("pinterest_300.jpg", "300"),
        ("pinterest_200.jpg", "200"),
    ):
        Pin.create(
            board=bid, rel_path=f"s/{filename}", filename=filename,
            pinterest_id=pinterest_id,
        )
    assert checkpoint_from_existing_pins(bid) == ["300", "200", "100"]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
class _FakePool:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args):
        self.jobs.append((name, args))


def _mk(status, auto):
    return Board.create(
        url="https://www.pinterest.com/u/b/", slug="s", status=status.value,
        auto_resync=auto,
    ).id


# --------------------------------------------------------------------------- #
# resync cron
# --------------------------------------------------------------------------- #
def test_resync_enqueues_only_idle_opted_in_boards():
    done_on = _mk(BoardStatus.done, True)
    error_on = _mk(BoardStatus.error, True)
    partial_on = _mk(BoardStatus.partial, True)
    _mk(BoardStatus.downloading, True)   # busy -> skip
    _mk(BoardStatus.queued, True)        # already queued -> skip
    _mk(BoardStatus.done, False)         # opted out -> skip

    pool = _FakePool()
    res = asyncio.run(resync_all_boards({"redis": pool}))

    assert res["enqueued"] == 3
    assert all(name == "download_board" for name, _ in pool.jobs)
    assert {args[0] for _, args in pool.jobs} == {done_on, error_on, partial_on}


def test_resync_no_queue():
    _mk(BoardStatus.done, True)
    res = asyncio.run(resync_all_boards({"redis": None}))
    assert res["enqueued"] == 0


# --------------------------------------------------------------------------- #
# board sync
# --------------------------------------------------------------------------- #
def test_sync_stops_at_saved_checkpoint(monkeypatch):
    from app.services.downloader import DownloadResult

    bid = _mk(BoardStatus.done, True)
    old = [str(n) for n in range(800, 790, -1)]
    Board.update(sync_checkpoint=json.dumps(old)).where(Board.id == bid).execute()

    captured = {}

    def fake_download(url, dest, **kwargs):
        captured["stop_pin_ids"] = kwargs["stop_pin_ids"]
        return DownloadResult(
            dest=dest, seen_pin_ids=["802", "801"],
            stopped_at_checkpoint=True, stop_pin_id="800",
        )

    monkeypatch.setattr(sync_service, "run_download", fake_download)
    result = sync_service.run_board_sync(bid)

    assert captured["stop_pin_ids"] == set(old)
    assert result["mode"] == "incremental"
    assert result["stopped_at_checkpoint"] is True
    board = Board.get_by_id(bid)
    assert decode_checkpoint(board.sync_checkpoint) == [
        "802", "801", "800", "799", "798", "797", "796", "795", "794", "793",
    ]
    assert board.status == BoardStatus.done.value


def test_forced_full_sync_replaces_checkpoint(monkeypatch):
    from app.services.downloader import DownloadResult

    bid = _mk(BoardStatus.done, True)
    Board.update(sync_checkpoint=json.dumps(["old"]), force_full_sync=True).where(
        Board.id == bid
    ).execute()

    captured = {}
    newest = [str(n) for n in range(100, 88, -1)]

    def fake_download(url, dest, **kwargs):
        captured["stop_pin_ids"] = kwargs["stop_pin_ids"]
        return DownloadResult(dest=dest, seen_pin_ids=newest)

    monkeypatch.setattr(sync_service, "run_download", fake_download)
    result = sync_service.run_board_sync(bid, force_full=True)

    assert captured["stop_pin_ids"] == set()
    assert result["mode"] == "full"
    board = Board.get_by_id(bid)
    assert decode_checkpoint(board.sync_checkpoint) == newest[:10]
    # Cleared so the next scheduled sync goes back to incremental.
    assert board.force_full_sync is False


def test_incremental_reconcile_preserves_cached_hashes(monkeypatch):
    from app.services.downloader import DownloadResult, MediaItem

    bid = _mk(BoardStatus.done, True)
    folder = board_folder("s", bid)
    Board.update(sync_checkpoint=json.dumps(["100"])).where(Board.id == bid).execute()
    Pin.create(
        board=bid, pinterest_id="100", filename="old.jpg",
        rel_path=f"{folder}/old.jpg", content_sha256="cached-sha",
        phash="cached-phash", file_size=123,
    )

    def fake_download(url, dest, **kwargs):
        return DownloadResult(
            dest=dest,
            media=[MediaItem(
                filename="old.jpg", rel_path="old.jpg", media_type="image",
                pinterest_id="100",
            )],
            stopped_at_checkpoint=True,
        )

    monkeypatch.setattr(sync_service, "run_download", fake_download)
    sync_service.run_board_sync(bid)

    pin = Pin.get(Pin.board == bid)
    assert pin.content_sha256 == "cached-sha"
    assert pin.phash == "cached-phash"
    assert pin.file_size == 123


def test_interrupted_scan_does_not_replace_checkpoint(monkeypatch):
    from app.services.downloader import DownloadResult

    bid = _mk(BoardStatus.done, True)
    previous = ["100", "99", "98"]
    Board.update(sync_checkpoint=json.dumps(previous)).where(Board.id == bid).execute()

    def fake_download(url, dest, **kwargs):
        # A few new ids were emitted, then extraction died before reaching any
        # old checkpoint. Saving these would make the next run stop too early.
        return DownloadResult(
            dest=dest, downloaded=1, errors=1, returncode=1,
            seen_pin_ids=["102", "101"], stopped_at_checkpoint=False,
        )

    monkeypatch.setattr(sync_service, "run_download", fake_download)
    sync_service.run_board_sync(bid)

    assert decode_checkpoint(Board.get_by_id(bid).sync_checkpoint) == previous


def test_tombstoned_pin_is_not_readded_by_sync(monkeypatch):
    """A duplicate the user deleted must not come back on the next sync."""
    from app.models import DeletedPin
    from app.services.downloader import DownloadResult, MediaItem

    bid = _mk(BoardStatus.done, True)
    folder = board_folder("s", bid)
    dest = settings.boards_dir / folder
    dest.mkdir(parents=True, exist_ok=True)
    (dest / "gone.jpg").write_bytes(b"x")
    DeletedPin.create(rel_path=f"{folder}/gone.jpg")

    def fake_download(url, dest_dir, **kwargs):
        return DownloadResult(
            dest=dest_dir,
            downloaded=1,
            media=[MediaItem(
                filename="gone.jpg", rel_path="gone.jpg", media_type="image",
            )],
        )

    monkeypatch.setattr(sync_service, "run_download", fake_download)
    sync_service.run_board_sync(bid)

    assert Pin.select().where(Pin.board == bid).count() == 0
    assert not (dest / "gone.jpg").exists()  # re-fetched file dropped again


def test_no_board_timeout():
    # Board-level timeout effectively removed; stalls handled per-pin instead.
    from app.worker import WorkerSettings

    assert WorkerSettings.job_timeout >= 24 * 3600


# --------------------------------------------------------------------------- #
# cron dispatch / resume
# --------------------------------------------------------------------------- #
def test_cron_dispatch_runs_due_schedules():
    from datetime import datetime

    _mk(BoardStatus.done, True)  # a resyncable board
    now = datetime.now(UTC)
    # A cron matching this exact minute -> resync should run; disable the others.
    due_expr = f"{now.minute} {now.hour} * * *"
    settings_service.save(
        {"resync_cron": due_expr, "refresh_cron": "", "dedup_cron": ""}
    )
    settings_service.set_raw("_last_resync_at", "")  # clear any prior last-run
    first = asyncio.run(_cron_dispatch({"redis": _FakePool()}))
    assert "resync_cron" in first                    # fired
    # Same minute -> the last-run guard must suppress a second run.
    second = asyncio.run(_cron_dispatch({"redis": _FakePool()}))
    assert second.get("skipped") == "nothing due"


def test_cron_dispatch_nothing_due_when_disabled():
    settings_service.save({"resync_cron": "", "refresh_cron": "", "dedup_cron": ""})
    res = asyncio.run(_cron_dispatch({"redis": _FakePool()}))
    assert res.get("skipped") == "nothing due"


def test_resume_interrupted_reenqueues_stuck_boards():
    d = _mk(BoardStatus.downloading, True)
    q = _mk(BoardStatus.queued, True)
    p = _mk(BoardStatus.pending, True)
    _mk(BoardStatus.done, True)   # settled -> not resumed
    pool = _FakePool()
    asyncio.run(_resume_interrupted({"redis": pool}))
    assert {args[0] for _, args in pool.jobs} == {d, q, p}


# --------------------------------------------------------------------------- #
# dedup + partial sync
# --------------------------------------------------------------------------- #
def test_recompute_duplicates_hashes_and_groups(make_image):
    img = make_image(size=(500, 400))
    # Two boards, each a physical copy of the same image -> should form a group.
    ids = []
    for slug in ("da", "db"):
        bid = Board.create(
            url="https://x/u/b/", slug=slug, status=BoardStatus.done.value,
        ).id
        folder = board_folder(slug, bid)
        dest = settings.boards_dir / folder
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(img, dest / "pin.jpg")
        ids.append(Pin.create(
            board=bid, rel_path=f"{folder}/pin.jpg", filename="pin.jpg",
            media_type="image",  # no hash yet
        ).id)

    res = recompute()
    assert res["hashed"] == 2 and res["groups"] == 1
    pins = [Pin.get_by_id(pid) for pid in ids]
    assert all(p.content_sha256 and p.phash for p in pins)
    # Both share one non-null dup_group.
    assert pins[0].dup_group is not None
    assert pins[0].dup_group == pins[1].dup_group


def test_recompute_failure_clears_running_status(monkeypatch):
    def fail_before_hashing():
        raise RuntimeError("broken media index")

    monkeypatch.setattr(dedup_service, "_pins_needing_hashes", fail_before_hashing)

    with pytest.raises(RuntimeError, match="broken media index"):
        dedup_service.recompute()

    status = dedup_service.get_status()
    assert status["running"] is False
    assert status["phase"] == "failed"
    assert status["error"] == "broken media index"


def test_stale_dedup_heartbeat_is_not_reported_as_running():
    old = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    settings_service.set_raw(
        "_dedup_status",
        json.dumps({
            "running": True,
            "phase": "hashing",
            "cur": 12,
            "total": 100,
            "updated_at": old,
        }),
    )

    status = dedup_service.get_status()

    assert status["running"] is False
    assert status["phase"] == "failed"
    assert "stopped" in status["error"]


def test_worker_restart_clears_live_dedup_status():
    dedup_service.set_status(running=True, phase="hashing", cur=5, total=10)

    assert dedup_service.recover_interrupted_status() is True
    status = dedup_service.get_status()
    assert status["running"] is False
    assert status["phase"] == "failed"
    assert "restart" in status["error"]


def test_hashless_image_is_not_rehashed_forever(monkeypatch):
    bid = Board.create(
        url="https://x/u/flat/", slug="flat", status=BoardStatus.done.value,
    ).id
    folder = board_folder("flat", bid)
    dest = settings.boards_dir / folder
    dest.mkdir(parents=True, exist_ok=True)
    path = dest / "flat.jpg"
    path.write_bytes(b"previously hashed but visually unusable")
    Pin.create(
        board=bid,
        rel_path=f"{folder}/flat.jpg",
        filename="flat.jpg",
        media_type="image",
        content_sha256="known-sha",
        phash=None,
    )

    def must_not_hash(*_args, **_kwargs):
        raise AssertionError("hashless image was rehashed")

    monkeypatch.setattr(dedup_service.dedup, "compute", must_not_hash)

    assert dedup_service.recompute()["hashed"] == 0


def test_sync_partial_pins_creates_rows_incrementally(make_image):
    bid = Board.create(
        url="https://x/u/b/", slug="s", status=BoardStatus.downloading.value,
    ).id
    folder = board_folder("s", bid)
    dest = settings.boards_dir / folder
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(make_image(), dest / "a.jpg")
    shutil.copy(make_image(), dest / "b.jpg")

    sync_service.sync_partial_pins(bid, folder, dest)
    pins = list(Pin.select().where(Pin.board == bid))
    assert {p.filename for p in pins} == {"a.jpg", "b.jpg"}
    # Light scan: no hashes computed yet.
    assert all(p.content_sha256 is None for p in pins)

    # A new file arrives; a second sync adds only it (no duplicates).
    shutil.copy(make_image(), dest / "c.jpg")
    sync_service.sync_partial_pins(bid, folder, dest)
    pins = list(Pin.select().where(Pin.board == bid))
    assert {p.filename for p in pins} == {"a.jpg", "b.jpg", "c.jpg"}
