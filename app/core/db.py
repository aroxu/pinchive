"""Peewee database handle, connection lifecycle, and schema migration.

SQLite via Peewee's ``SqliteDatabase``. The web process serves ``def`` routes
from a threadpool and the arq worker is async, so the connection is opened and
closed per unit of work (request / job / task) rather than held globally --
``SqliteDatabase`` keeps one connection per thread in thread-local storage.

WAL is enabled through ``pragmas`` so the web process and the worker can read
and write the same file concurrently.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from peewee import SqliteDatabase

from app.core.config import get_settings

# `pragmas` are applied to every new connection, so WAL/busy_timeout hold for
# both processes. autoconnect=False makes connection lifetime explicit: a leaked
# connection in a threadpool worker would otherwise pin a stale snapshot.
database = SqliteDatabase(
    None,
    pragmas={
        "journal_mode": "wal",
        "synchronous": 1,          # NORMAL
        "busy_timeout": 5000,
        "foreign_keys": 1,
    },
    autoconnect=False,
)

_initialised = False


def bind() -> SqliteDatabase:
    """Point the deferred database at the configured data dir (idempotent)."""
    global _initialised
    if not _initialised:
        settings = get_settings()
        settings.ensure_dirs()
        database.init(str(settings.db_path))
        _initialised = True
    return database


@contextmanager
def connection() -> Iterator[SqliteDatabase]:
    """Borrow a connection for one unit of work.

    Reentrant: a nested ``with connection()`` reuses the outer connection and
    leaves closing to the outermost caller, so services can compose freely.
    """
    db = bind()
    if not db.is_closed():
        yield db
        return
    db.connect()
    try:
        yield db
    finally:
        if not db.is_closed():
            db.close()


@contextmanager
def transaction() -> Iterator[SqliteDatabase]:
    """Connection + atomic block: commits on success, rolls back on error."""
    with connection() as db, db.atomic():
        yield db


def create_schema(tables) -> None:
    """Create missing tables, then patch in columns added after a release.

    Takes the table list as an argument rather than importing the models: `core`
    sits below `models` in the dependency order, so it must not reach upward.
    Call it through :func:`app.models.init_db`.
    """
    with connection() as db:
        db.create_tables(tables, safe=True)
        _migrate(db)


# Columns added after the first release. create_tables() will not ALTER an
# existing table, so patch them into databases made by an older version.
# (No Alembic/peewee-migrate: the whole schema is one file.)
_ADDED_COLUMNS = {
    "pin": {
        "title": "VARCHAR",
        "description": "VARCHAR",
        "content_sha256": "VARCHAR",
        "phash": "VARCHAR",
        "file_size": "INTEGER",
        "dup_group": "INTEGER",
        "storage_backend": "VARCHAR DEFAULT 'local'",
    },
    "board": {
        "auto_resync": "BOOLEAN DEFAULT 1",
        "sync_checkpoint": "VARCHAR",
        "force_full_sync": "BOOLEAN DEFAULT 0",
    },
}

_INDEXES = (
    "CREATE INDEX IF NOT EXISTS ix_pin_content_sha256 ON pin (content_sha256)",
    "CREATE INDEX IF NOT EXISTS ix_pin_phash ON pin (phash)",
    "CREATE INDEX IF NOT EXISTS ix_pin_dup_group ON pin (dup_group)",
)


def _migrate(db: SqliteDatabase) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {row[1] for row in db.execute_sql(f"PRAGMA table_info({table})")}
        if not existing:
            continue  # table absent entirely -> create_tables already made it
        for name, sqltype in columns.items():
            if name not in existing:
                db.execute_sql(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")
    for stmt in _INDEXES:
        db.execute_sql(stmt)
