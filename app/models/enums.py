"""Status vocabularies shared by models, services and the API layer."""

from __future__ import annotations

from enum import Enum


class BoardStatus(str, Enum):
    pending = "pending"        # created, not yet queued
    queued = "queued"          # handed to the worker
    downloading = "downloading"
    done = "done"
    partial = "partial"        # finished but some pins failed
    error = "error"


class CredentialStatus(str, Enum):
    unchecked = "unchecked"
    active = "active"
    expired = "expired"
    error = "error"


class MediaType(str, Enum):
    image = "image"
    video = "video"


class StorageBackend(str, Enum):
    """Where a pin's bytes live. Only ``local`` is implemented today; ``s3`` is
    reserved by the storage plan in docs/storage-backends.md so the column and
    the API contract do not need a breaking change later."""

    local = "local"
    s3 = "s3"


# User-facing board state collapses the six internal statuses into two.
STATUS_GROUPS: dict[str, list[BoardStatus]] = {
    "working": [BoardStatus.pending, BoardStatus.queued, BoardStatus.downloading],
    "done": [BoardStatus.done, BoardStatus.partial, BoardStatus.error],
}

ACTIVE_STATUSES = frozenset(STATUS_GROUPS["working"])
