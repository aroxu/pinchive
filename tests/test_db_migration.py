"""The hand-rolled sqlite column migration (Peewee)."""

from app.core.db import _migrate, connection
from app.models import init_db


def _cols(table: str) -> set[str]:
    with connection() as db:
        return {row[1] for row in db.execute_sql(f"PRAGMA table_info({table})")}


def test_new_columns_present_after_init():
    init_db()
    cols = _cols("pin")
    for c in ("title", "description", "content_sha256", "phash", "file_size"):
        assert c in cols


def test_migrate_is_idempotent():
    init_db()
    with connection() as db:
        _migrate(db)
        _migrate(db)  # second run must not raise
    assert "phash" in _cols("pin")


def test_migrate_adds_missing_columns_to_old_table():
    # Simulate a pre-feature DB: a bare pin table missing the new columns.
    with connection() as db:
        db.execute_sql("DROP TABLE IF EXISTS pin")
        db.execute_sql(
            "CREATE TABLE pin (id INTEGER PRIMARY KEY, board_id INTEGER, "
            "filename VARCHAR, rel_path VARCHAR)"
        )
        assert "content_sha256" not in _cols("pin")
        _migrate(db)
    assert {
        "content_sha256", "phash", "file_size", "title", "description",
        "dup_group", "storage_backend",
    } <= _cols("pin")


def test_migrate_adds_board_sync_columns():
    with connection() as db:
        db.execute_sql("DROP TABLE IF EXISTS board")
        db.execute_sql(
            "CREATE TABLE board (id INTEGER PRIMARY KEY, url VARCHAR, status VARCHAR)"
        )
        assert "auto_resync" not in _cols("board")
        _migrate(db)
    assert {"auto_resync", "sync_checkpoint", "force_full_sync"} <= _cols("board")
