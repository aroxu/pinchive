"""Slug + per-board folder derivation, and the auto-resync cron."""

import asyncio
import shutil

from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.models import Board, BoardStatus, Pin
from app.tasks import _sync_partial_pins, board_folder, derive_slug, resync_all_boards


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


# --- config schedule ---
def test_resync_hours_enabled_and_disabled():
    s = get_settings()
    s.resync_every_hours = 12
    assert s.resync_hours() == {0, 12}
    s.resync_every_hours = 0
    assert s.resync_hours() == set()  # disabled
    s.resync_every_hours = 24  # restore default
    assert s.resync_hours() == {0}


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


def test_no_board_timeout():
    # board-level timeout effectively removed; stalls handled per-pin instead
    from app.tasks import WorkerSettings
    assert WorkerSettings.job_timeout >= 24 * 3600


def test_cron_resync_gates_on_interval():
    from app import appsettings
    from app.tasks import _cron_resync

    appsettings.save({"resync_every_hours": "0"})        # disabled
    r = asyncio.run(_cron_resync({"redis": _FakePool()}))
    assert r.get("skipped") == "disabled"

    appsettings.save({"resync_every_hours": "6"})        # enabled, never run -> due
    r = asyncio.run(_cron_resync({"redis": _FakePool()}))
    assert "skipped" not in r                            # ran (updated last-run)

    r = asyncio.run(_cron_resync({"redis": _FakePool()}))  # just ran -> not due
    assert r.get("skipped") == "not due"


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
