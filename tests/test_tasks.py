"""Slug + per-board folder derivation, and the auto-resync cron."""

import asyncio
import json
import shutil

from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.models import Board, BoardStatus, Pin
from app.tasks import (
    _checkpoint_from_existing_pins,
    _decode_sync_checkpoint,
    _merge_sync_checkpoint,
    _sync_partial_pins,
    board_folder,
    derive_slug,
    resync_all_boards,
)


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


def test_sync_checkpoint_merge_is_unique_and_capped():
    previous = [str(n) for n in range(20, 10, -1)]
    merged = _merge_sync_checkpoint(["22", "21", "20", "22"], previous)
    assert merged == ["22", "21", "20", "19", "18", "17", "16", "15", "14", "13"]
    assert _decode_sync_checkpoint("not-json") == []
    assert _decode_sync_checkpoint(json.dumps(["2", "2", "1"])) == ["2", "1"]


def test_checkpoint_bootstraps_from_existing_newest_pins():
    bid = _mk(BoardStatus.done, True)
    with session_scope() as s:
        for filename, pinterest_id in (
            ("pinterest_100.jpg", "100"),
            ("pinterest_300.jpg", "300"),
            ("pinterest_200.jpg", "200"),
        ):
            s.add(Pin(
                board_id=bid, rel_path=f"s/{filename}", filename=filename,
                pinterest_id=pinterest_id,
            ))
    with session_scope() as s:
        assert _checkpoint_from_existing_pins(s, bid) == ["300", "200", "100"]


# --- resync cron ---
class _FakePool:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args):
        self.jobs.append((name, args))


def _mk(status, auto):
    with session_scope() as s:
        b = Board(url="https://www.pinterest.com/u/b/", slug="s", status=status,
                  auto_resync=auto)
        s.add(b)
        s.flush()
        return b.id


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
    enq_ids = {args[0] for _, args in pool.jobs}
    assert enq_ids == {done_on, error_on, partial_on}


def test_resync_no_queue():
    _mk(BoardStatus.done, True)
    res = asyncio.run(resync_all_boards({"redis": None}))
    assert res["enqueued"] == 0


def test_download_board_stops_at_saved_checkpoint(monkeypatch):
    from app import tasks
    from app.downloader import DownloadResult

    bid = _mk(BoardStatus.done, True)
    old = [str(n) for n in range(800, 790, -1)]
    with session_scope() as s:
        s.get(Board, bid).sync_checkpoint = json.dumps(old)

    captured = {}

    def fake_download(url, dest, **kwargs):
        captured["stop_pin_ids"] = kwargs["stop_pin_ids"]
        return DownloadResult(
            dest=dest, seen_pin_ids=["802", "801"],
            stopped_at_checkpoint=True, stop_pin_id="800",
        )

    monkeypatch.setattr(tasks, "run_download", fake_download)
    result = asyncio.run(tasks.download_board({}, bid))

    assert captured["stop_pin_ids"] == set(old)
    assert result["mode"] == "incremental"
    assert result["stopped_at_checkpoint"] is True
    with session_scope() as s:
        board = s.get(Board, bid)
        assert _decode_sync_checkpoint(board.sync_checkpoint) == [
            "802", "801", "800", "799", "798", "797", "796", "795", "794", "793"
        ]
        assert board.status == BoardStatus.done


def test_download_board_force_full_replaces_checkpoint(monkeypatch):
    from app import tasks
    from app.downloader import DownloadResult

    bid = _mk(BoardStatus.done, True)
    with session_scope() as s:
        board = s.get(Board, bid)
        board.sync_checkpoint = json.dumps(["old"])
        board.force_full_sync = True

    captured = {}
    newest = [str(n) for n in range(100, 88, -1)]

    def fake_download(url, dest, **kwargs):
        captured["stop_pin_ids"] = kwargs["stop_pin_ids"]
        return DownloadResult(dest=dest, seen_pin_ids=newest)

    monkeypatch.setattr(tasks, "run_download", fake_download)
    result = asyncio.run(tasks.download_board({}, bid, True))

    assert captured["stop_pin_ids"] == set()
    assert result["mode"] == "full"
    with session_scope() as s:
        board = s.get(Board, bid)
        assert _decode_sync_checkpoint(board.sync_checkpoint) == newest[:10]
        assert board.force_full_sync is False


def test_incremental_reconcile_preserves_cached_hashes(monkeypatch):
    from app import tasks
    from app.downloader import DownloadResult, MediaItem

    bid = _mk(BoardStatus.done, True)
    folder = board_folder("s", bid)
    with session_scope() as s:
        board = s.get(Board, bid)
        board.sync_checkpoint = json.dumps(["100"])
        s.add(Pin(
            board_id=bid, pinterest_id="100", filename="old.jpg",
            rel_path=f"{folder}/old.jpg", content_sha256="cached-sha",
            phash="cached-phash", file_size=123,
        ))

    def fake_download(url, dest, **kwargs):
        return DownloadResult(
            dest=dest,
            media=[MediaItem(
                filename="old.jpg", rel_path="old.jpg", media_type="image",
                pinterest_id="100",
            )],
            stopped_at_checkpoint=True,
        )

    monkeypatch.setattr(tasks, "run_download", fake_download)
    asyncio.run(tasks.download_board({}, bid))

    with session_scope() as s:
        pin = s.exec(select(Pin).where(Pin.board_id == bid)).one()
        assert pin.content_sha256 == "cached-sha"
        assert pin.phash == "cached-phash"
        assert pin.file_size == 123


def test_interrupted_scan_does_not_replace_checkpoint(monkeypatch):
    from app import tasks
    from app.downloader import DownloadResult

    bid = _mk(BoardStatus.done, True)
    previous = ["100", "99", "98"]
    with session_scope() as s:
        s.get(Board, bid).sync_checkpoint = json.dumps(previous)

    def fake_download(url, dest, **kwargs):
        # A few new ids were emitted, then extraction died before reaching any
        # old checkpoint. Saving these would make the next run stop too early.
        return DownloadResult(
            dest=dest, downloaded=1, errors=1, returncode=1,
            seen_pin_ids=["102", "101"], stopped_at_checkpoint=False,
        )

    monkeypatch.setattr(tasks, "run_download", fake_download)
    asyncio.run(tasks.download_board({}, bid))

    with session_scope() as s:
        assert _decode_sync_checkpoint(s.get(Board, bid).sync_checkpoint) == previous


def test_no_board_timeout():
    # board-level timeout effectively removed; stalls handled per-pin instead
    from app.tasks import WorkerSettings
    assert WorkerSettings.job_timeout >= 24 * 3600


def test_cron_dispatch_runs_due_schedules():
    from datetime import datetime, timezone

    from app import appsettings
    from app.tasks import _cron_dispatch

    _mk(BoardStatus.done, True)  # a resyncable board
    now = datetime.now(timezone.utc)
    # a cron matching this exact minute -> resync should run; disable the others
    due_expr = f"{now.minute} {now.hour} * * *"
    appsettings.save({"resync_cron": due_expr, "refresh_cron": "", "dedup_cron": ""})
    # clear any prior last-run so it's due
    appsettings.set_raw("_last_resync_at", "")
    r = asyncio.run(_cron_dispatch({"redis": _FakePool()}))
    assert "resync_cron" in r                       # fired
    r2 = asyncio.run(_cron_dispatch({"redis": _FakePool()}))  # same minute -> guard
    assert r2.get("skipped") == "nothing due"


def test_cron_dispatch_nothing_due_when_disabled():
    from app import appsettings
    from app.tasks import _cron_dispatch

    appsettings.save({"resync_cron": "", "refresh_cron": "", "dedup_cron": ""})
    r = asyncio.run(_cron_dispatch({"redis": _FakePool()}))
    assert r.get("skipped") == "nothing due"


def test_resume_interrupted_reenqueues_stuck_boards():
    from app.tasks import _resume_interrupted
    d = _mk(BoardStatus.downloading, True)
    q = _mk(BoardStatus.queued, True)
    p = _mk(BoardStatus.pending, True)
    _mk(BoardStatus.done, True)   # settled -> not resumed
    pool = _FakePool()
    asyncio.run(_resume_interrupted({"redis": pool}))
    assert {args[0] for _, args in pool.jobs} == {d, q, p}


def test_recompute_duplicates_hashes_and_groups(make_image):
    from app.tasks import _recompute_duplicates_blocking
    s = get_settings()
    img = make_image(size=(500, 400))
    # two boards, each a physical copy of the same image -> should form a group
    ids = []
    for slug in ("da", "db"):
        with session_scope() as sess:
            b = Board(url="https://x/u/b/", slug=slug, status=BoardStatus.done)
            sess.add(b)
            sess.flush()
            bid = b.id
        folder = board_folder(slug, bid)
        dest = s.boards_dir / folder
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copy(img, dest / "pin.jpg")
        with session_scope() as sess:
            p = Pin(board_id=bid, rel_path=f"{folder}/pin.jpg", filename="pin.jpg",
                    media_type="image")  # no hash yet
            sess.add(p)
            sess.flush()
            ids.append(p.id)

    res = _recompute_duplicates_blocking()
    assert res["hashed"] == 2 and res["groups"] == 1
    with session_scope() as sess:
        pins = [sess.get(Pin, pid) for pid in ids]
        assert all(p.content_sha256 and p.phash for p in pins)
        # both share one non-null dup_group
        assert pins[0].dup_group is not None
        assert pins[0].dup_group == pins[1].dup_group


def test_sync_partial_pins_creates_rows_incrementally(make_image):
    s = get_settings()
    with session_scope() as sess:
        b = Board(url="https://x/u/b/", slug="s", status=BoardStatus.downloading)
        sess.add(b)
        sess.flush()
        bid = b.id
    folder = board_folder("s", bid)
    dest = s.boards_dir / folder
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy(make_image(), dest / "a.jpg")
    shutil.copy(make_image(), dest / "b.jpg")

    _sync_partial_pins(bid, folder, dest)
    with session_scope() as sess:
        pins = sess.exec(select(Pin).where(Pin.board_id == bid)).all()
        assert {p.filename for p in pins} == {"a.jpg", "b.jpg"}
        assert all(p.content_sha256 is None for p in pins)  # light, no hashes yet

    # a new file arrives; a second sync adds only it (no duplicates)
    shutil.copy(make_image(), dest / "c.jpg")
    _sync_partial_pins(bid, folder, dest)
    with session_scope() as sess:
        pins = sess.exec(select(Pin).where(Pin.board_id == bid)).all()
        assert {p.filename for p in pins} == {"a.jpg", "b.jpg", "c.jpg"}
