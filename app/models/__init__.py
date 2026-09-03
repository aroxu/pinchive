"""Peewee data model.

Table/column names mirror the original schema so an existing ``pinchive.db``
opens without a data migration. ``ALL_TABLES`` drives ``create_tables``.
"""

from app.core.db import create_schema
from app.models.archive import (
    Board,
    BoardTagLink,
    Credential,
    DeletedPin,
    Pin,
    PinTagLink,
    Setting,
    Tag,
    readable_name_from_url,
)
from app.models.base import BaseModel, UTCDateTimeField, utcnow
from app.models.enums import (
    ACTIVE_STATUSES,
    STATUS_GROUPS,
    BoardStatus,
    CredentialStatus,
    MediaType,
    StorageBackend,
)

ALL_TABLES = (
    Credential,
    Board,
    Pin,
    Tag,
    BoardTagLink,
    PinTagLink,
    Setting,
    DeletedPin,
)


def init_db() -> None:
    """Bring the database up to date with these models."""
    create_schema(ALL_TABLES)


__all__ = [
    "ACTIVE_STATUSES",
    "ALL_TABLES",
    "STATUS_GROUPS",
    "BaseModel",
    "Board",
    "BoardStatus",
    "BoardTagLink",
    "Credential",
    "CredentialStatus",
    "DeletedPin",
    "MediaType",
    "Pin",
    "PinTagLink",
    "Setting",
    "StorageBackend",
    "Tag",
    "UTCDateTimeField",
    "init_db",
    "readable_name_from_url",
    "utcnow",
]
