"""The hand-rolled sqlite column migration."""

from sqlalchemy import inspect, text

from app.db import _migrate, engine, init_db


def _pin_cols():
    return {c["name"] for c in inspect(engine).get_columns("pin")}


def test_new_columns_present_after_init():
    init_db()
    cols = _pin_cols()
    for c in ("title", "description", "content_sha256", "phash", "file_size"):
        assert c in cols


def test_migrate_is_idempotent():
    init_db()
    _migrate()
    _migrate()  # second run must not raise
    assert "phash" in _pin_cols()


def test_migrate_adds_missing_columns_to_old_table():
    # Simulate a pre-feature DB: a bare pin table missing the new columns.
    with engine.begin() as c:
        c.execute(text("DROP TABLE IF EXISTS pin"))
        c.execute(text(
            "CREATE TABLE pin (id INTEGER PRIMARY KEY, board_id INTEGER, "
            "filename VARCHAR, rel_path VARCHAR)"
        ))
    assert "content_sha256" not in _pin_cols()
    _migrate()
    cols = _pin_cols()
    assert {"content_sha256", "phash", "file_size", "title", "description"} <= cols
