"""Recovery contracts exercised through worker jobs and the real sync service."""

import asyncio
import json
import shutil

import pytest

from app import worker
from app.models import Board, BoardStatus, Pin
from app.services import board_service, sync_service
from app.services.board_service import decode_checkpoint
from app.services.downloader import DownloadResult, MediaItem, Progress

_CHECKPOINT = ["100", "99", "98"]


class _RecordingPool:
    def __init__(self):
        self.jobs = []

    async def enqueue_job(self, name, *args):
        self.jobs.append((name, args))


def _board(status, *, force_full, auto_resync=False):
    return Board.create(
        url="https://www.pinterest.com/recovery/edges/",
        slug="recovery",
        status=status.value,
        force_full_sync=force_full,
        sync_checkpoint=json.dumps(_CHECKPOINT),
        auto_resync=auto_resync,
    ).id


def _execute_job(pool):
    assert len(pool.jobs) == 1
    name, args = pool.jobs[0]
    assert name == "download_board"
    return asyncio.run(worker.download_board({}, *args))


@pytest.fixture(autouse=True)
def simulated_download_only(monkeypatch):
    def unexpected_download(*args, **kwargs):
        raise AssertionError("recovery tests must supply a simulated downloader")

    monkeypatch.setattr(sync_service, "run_download", unexpected_download)


@pytest.mark.parametrize("status", [
    BoardStatus.pending, BoardStatus.queued, BoardStatus.downloading,
])
@pytest.mark.parametrize("force_full", [False, True])
def test_repeated_restarts_preserve_mode_until_job_finishes(monkeypatch, status, force_full):
    bid = _board(status, force_full=force_full)
    raw_checkpoint = Board.get_by_id(bid).sync_checkpoint
    calls = []

    def fake_download(url, dest, **kwargs):
        board = board_service.get_board(bid)
        assert board.status == BoardStatus.downloading.value
        assert board.force_full_sync is force_full
        assert board.sync_checkpoint == raw_checkpoint
        assert kwargs["cookies_file"] is None
        calls.append(kwargs["stop_pin_ids"])
        return DownloadResult(
            dest=dest,
            seen_pin_ids=["102", "101"],
            stopped_at_checkpoint=not force_full,
            stop_pin_id=None if force_full else _CHECKPOINT[0],
        )

    monkeypatch.setattr(sync_service, "run_download", fake_download)

    # Each worker dies before consuming its queued job. Later startups must
    # still recover the same intent, including when the board is already queued.
    for _ in range(3):
        pool = _RecordingPool()
        asyncio.run(worker._resume_interrupted({"redis": pool}))
        assert pool.jobs == [("download_board", (bid,))]
        board = Board.get_by_id(bid)
        assert board.status == BoardStatus.queued.value
        assert board.force_full_sync is force_full
        assert board.sync_checkpoint == raw_checkpoint
        assert not calls

    result = _execute_job(pool)

    assert calls == [set() if force_full else set(_CHECKPOINT)]
    assert result["mode"] == ("full" if force_full else "incremental")
    assert result["status"] == "done"
    board = Board.get_by_id(bid)
    assert board.status == BoardStatus.done.value
    assert board.force_full_sync is False
    expected = ["102", "101"] + ([] if force_full else _CHECKPOINT)
    assert decode_checkpoint(board.sync_checkpoint) == expected

    pool = _RecordingPool()
    asyncio.run(worker._resume_interrupted({"redis": pool}))
    assert pool.jobs == []


@pytest.mark.parametrize("runner", [worker._resume_interrupted, worker.resync_all_boards])
@pytest.mark.parametrize("ctx", [{}, {"redis": None}], ids=["missing", "none"])
def test_queue_absent_leaves_all_board_state_unchanged(runner, ctx):
    # Include both the interrupted and settled statuses so moving the no-queue
    # guard below any bookkeeping cannot silently abandon or reset a full scan.
    for status in BoardStatus:
        for force_full in (False, True):
            bid = _board(status, force_full=force_full, auto_resync=True)
            Board.update(
                last_error="keep the previous failure",
                log_tail="existing download log",
                downloaded_count=3,
                skipped_count=4,
                error_count=2,
                pin_count=3,
            ).where(Board.id == bid).execute()
    before = list(Board.select().order_by(Board.id).dicts())

    result = asyncio.run(runner(ctx))

    if runner is worker.resync_all_boards:
        assert result == {"enqueued": 0, "error": "no queue"}
    assert list(Board.select().order_by(Board.id).dicts()) == before


@pytest.mark.parametrize(("returncode", "partial"), [
    pytest.param(1, False, id="extractor-failure"),
    pytest.param(-15, False, id="terminated-process"),
    pytest.param(1, True, id="failure-after-media"),
])
def test_returned_full_scan_failure_clears_intent_and_next_cron_is_incremental(
    monkeypatch, make_image, returncode, partial,
):
    bid = _board(BoardStatus.queued, force_full=True, auto_resync=True)
    raw_checkpoint = Board.get_by_id(bid).sync_checkpoint
    image = make_image(size=(16, 16))
    calls = []
    media = []

    def fake_download(url, dest, **kwargs):
        assert kwargs["cookies_file"] is None
        calls.append(kwargs["stop_pin_ids"])
        if len(calls) == 1:
            assert board_service.get_board(bid).force_full_sync is True
            if partial:
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image, dest / "102.jpg")
                media.append(MediaItem(
                    filename="102.jpg", rel_path="102.jpg", media_type="image",
                    pinterest_id="102",
                ))
            return DownloadResult(
                dest=dest, returncode=returncode, errors=1,
                downloaded=int(partial), media=media,
                seen_pin_ids=["102", "101"],
                log_tail="[pinterest][error] simulated forced scan failure",
            )
        assert board_service.get_board(bid).force_full_sync is False
        return DownloadResult(
            dest=dest, media=media, seen_pin_ids=["102", "101"],
            stopped_at_checkpoint=True, stop_pin_id=_CHECKPOINT[0],
        )

    monkeypatch.setattr(sync_service, "run_download", fake_download)
    result = asyncio.run(worker.download_board({}, bid))

    assert result["mode"] == "full"
    assert result["status"] == ("partial" if partial else "error")
    assert result["stopped_at_checkpoint"] is False
    board = Board.get_by_id(bid)
    assert board.force_full_sync is False
    assert board.sync_checkpoint == raw_checkpoint
    assert board.finished_at is not None
    assert board.error_count == 1
    assert board.pin_count == int(partial)
    assert board.status == (BoardStatus.done.value if partial else BoardStatus.error.value)
    if partial:
        assert board.last_error is None
    else:
        assert "simulated forced scan failure" in board.last_error

    pool = _RecordingPool()
    assert asyncio.run(worker.resync_all_boards({"redis": pool})) == {"enqueued": 1}
    assert Board.get_by_id(bid).sync_checkpoint == raw_checkpoint
    next_result = _execute_job(pool)

    assert calls == [set(), set(_CHECKPOINT)]
    assert next_result["mode"] == "incremental"
    assert next_result["status"] == "done"
    assert decode_checkpoint(Board.get_by_id(bid).sync_checkpoint) == [
        "102", "101", *_CHECKPOINT,
    ]


@pytest.mark.parametrize("exception_type", [OSError, RuntimeError])
@pytest.mark.parametrize("after_progress", [False, True], ids=["launch", "mid-download"])
def test_exception_before_download_returns_preserves_intent_for_repeated_restart(
    monkeypatch, make_image, exception_type, after_progress,
):
    bid = _board(BoardStatus.queued, force_full=True)
    raw_checkpoint = Board.get_by_id(bid).sync_checkpoint
    image = make_image(size=(16, 16))
    calls = []

    def fake_download(url, dest, **kwargs):
        calls.append(kwargs["stop_pin_ids"])
        assert board_service.get_board(bid).force_full_sync is True
        assert kwargs["cookies_file"] is None
        if len(calls) == 1:
            if after_progress:
                dest.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(image, dest / "102.jpg")
                (dest / "102.jpg.json").write_text(json.dumps({"id": "102"}))
                kwargs["on_progress"](Progress(downloaded=1))
            raise exception_type("simulated interruption before downloader returned")
        return DownloadResult(
            dest=dest, seen_pin_ids=["102", "101"],
            media=sync_service.scan_media(dest, with_hashes=False),
        )

    monkeypatch.setattr(sync_service, "run_download", fake_download)

    with pytest.raises(exception_type, match="before downloader returned"):
        asyncio.run(worker.download_board({}, bid))

    board = Board.get_by_id(bid)
    assert board.status == BoardStatus.downloading.value
    assert board.force_full_sync is True
    assert board.sync_checkpoint == raw_checkpoint
    assert board.started_at is not None
    assert board.finished_at is None
    assert board.downloaded_count == int(after_progress)
    partial_pin_ids = [p.id for p in Pin.select().where(Pin.board == bid)]
    assert len(partial_pin_ids) == int(after_progress)

    for _ in range(2):
        pool = _RecordingPool()
        asyncio.run(worker._resume_interrupted({"redis": pool}))
        assert pool.jobs == [("download_board", (bid,))]
        board = Board.get_by_id(bid)
        assert board.status == BoardStatus.queued.value
        assert board.force_full_sync is True
        assert board.sync_checkpoint == raw_checkpoint

    result = _execute_job(pool)

    assert calls == [set(), set()]
    assert result["mode"] == "full"
    assert result["status"] == "done"
    board = Board.get_by_id(bid)
    assert board.status == BoardStatus.done.value
    assert board.force_full_sync is False
    assert decode_checkpoint(board.sync_checkpoint) == ["102", "101"]
    assert [p.id for p in Pin.select().where(Pin.board == bid)] == partial_pin_ids
