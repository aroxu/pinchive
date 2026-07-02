"""Database engine + session helpers.

Synchronous SQLModel over SQLite. FastAPI route handlers declared with `def`
run in a threadpool, so blocking DB calls are safe there; the arq worker is
async but SQLite calls are sub-millisecond for this workload, so we call the
sync session directly rather than pulling in an async driver.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_settings = get_settings()
_settings.ensure_dirs()

engine = create_engine(
    _settings.db_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(Engine, "connect")
def _sqlite_pragmas(dbapi_conn, _record) -> None:  # noqa: ANN001
    """WAL + reasonable durability for a concurrent web+worker setup."""
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()


def init_db() -> None:
    # Import models so metadata is populated before create_all.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _migrate()


# Columns added after the first release. create_all won't ALTER existing tables,
# so patch them in for DBs created by an older version. (No Alembic — one file.)
_ADDED_COLUMNS = {
    "pin": {
        "title": "VARCHAR",
        "description": "VARCHAR",
        "content_sha256": "VARCHAR",
        "phash": "VARCHAR",
        "file_size": "INTEGER",
    },
}


def _migrate() -> None:
    from sqlalchemy import text

    with engine.begin() as conn:
        for table, cols in _ADDED_COLUMNS.items():
            existing = {
                row[1]
                for row in conn.execute(text(f"PRAGMA table_info({table})"))
            }
            for name, sqltype in cols.items():
                if name not in existing:
                    conn.execute(
                        text(f"ALTER TABLE {table} ADD COLUMN {name} {sqltype}")
                    )
        # Helpful indexes for the new dedup columns (safe if already present).
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_pin_content_sha256 "
                 "ON pin (content_sha256)")
        )
        conn.execute(
            text("CREATE INDEX IF NOT EXISTS ix_pin_phash ON pin (phash)")
        )


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional session for worker / scripts."""
    session = Session(engine)
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency."""
    with Session(engine) as session:
        yield session
