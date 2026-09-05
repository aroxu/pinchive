import asyncio
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from arq import Retry
from peewee import OperationalError

from app.core.config import get_settings
from app.core.db import connection
from app.models import Board, BoardStatus
from app.services import sync_service
from app.worker import download_board


def test_write_transaction_waits_for_an_existing_writer():
    """A worker starting while the web process writes must wait, not fail.

    The other connection takes SQLite's writer reservation first. A deferred
    transaction would read the board and then fail immediately while upgrading;
    BEGIN IMMEDIATE waits until the reservation is released.
    """
    board = Board.create(
        url="https://www.pinterest.com/u/b/",
        slug="u__b",
        status=BoardStatus.queued.value,
    )
    locker = sqlite3.connect(get_settings().db_path, isolation_level=None)
    locker.execute("BEGIN IMMEDIATE")
    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            pending = executor.submit(sync_service.start_sync, board.id)
            time.sleep(0.15)
            assert not pending.done()
            locker.execute("COMMIT")
            plan = pending.result(timeout=2)
    finally:
        if locker.in_transaction:
            locker.execute("ROLLBACK")
        locker.close()

    assert plan is not None
    assert Board.get_by_id(board.id).status == BoardStatus.downloading.value


def test_connections_use_a_thirty_second_busy_timeout():
    with connection() as db:
        timeout_ms = db.execute_sql("PRAGMA busy_timeout").fetchone()[0]
    assert timeout_ms == 30_000


def test_download_job_retries_a_residual_database_lock(monkeypatch):
    def locked(*args, **kwargs):
        raise OperationalError("database is locked")

    monkeypatch.setattr(sync_service, "run_board_sync", locked)
    with pytest.raises(Retry) as caught:
        asyncio.run(download_board({"job_try": 3}, 1))
    assert caught.value.defer_score == 4_000
