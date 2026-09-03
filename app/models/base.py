"""Shared Peewee base model and column helpers.

Table names are pinned to the singular lower-case names the original schema
used (``board``, ``pin``, ``credential``, ...) so an existing ``pinchive.db``
opens unchanged after the move off SQLAlchemy.
"""

from __future__ import annotations

from datetime import UTC, datetime

from peewee import DateTimeField, Model

from app.core.db import database


def utcnow() -> datetime:
    return datetime.now(UTC)


class UTCDateTimeField(DateTimeField):
    """Stores naive-UTC (matching the pre-existing rows) and reads back aware.

    SQLite has no timezone type and the previous ORM wrote naive UTC strings.
    Normalising on the way in and attaching UTC on the way out keeps old and new
    rows comparable without a data migration.
    """

    def db_value(self, value):
        if isinstance(value, datetime) and value.tzinfo is not None:
            value = value.astimezone(UTC).replace(tzinfo=None)
        return super().db_value(value)

    def python_value(self, value):
        value = super().python_value(value)
        if isinstance(value, datetime) and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class BaseModel(Model):
    class Meta:
        database = database
        legacy_table_names = False
