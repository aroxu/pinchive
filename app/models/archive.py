"""Archive domain tables: credentials, boards, pins, tags, tombstones."""

from __future__ import annotations

from urllib.parse import unquote, urlparse

from peewee import (
    BooleanField,
    CharField,
    CompositeKey,
    ForeignKeyField,
    IntegerField,
    TextField,
)

from app.models.base import BaseModel, UTCDateTimeField, utcnow
from app.models.enums import BoardStatus, CredentialStatus, MediaType, StorageBackend


def readable_name_from_url(url: str) -> str | None:
    """Turn a board URL's name segment into a human label, decoding percent-
    encoded Unicode (e.g. ``%EA%B7%B8%EB%A6%BC`` -> ``그림``) so Korean/other
    non-ASCII board names read properly instead of as encoded gibberish."""
    parts = [p for p in urlparse(url).path.strip("/").split("/") if p]
    if not parts:
        return None
    seg = parts[1] if len(parts) >= 2 else parts[0]
    seg = unquote(seg).replace("-", " ").replace("_", " ").strip()
    return seg or None


class Credential(BaseModel):
    """A stored Pinterest session (cookies) used to reach private boards.

    The cookies themselves live on disk as a Netscape cookies.txt at
    ``settings.cookies_dir / f"{id}.txt"`` -- not in the DB -- so gallery-dl can
    consume the file directly and secrets stay out of the sqlite dump.
    """

    name = CharField(index=True)
    status = CharField(default=CredentialStatus.unchecked.value)
    note = CharField(null=True)
    last_checked_at = UTCDateTimeField(null=True)
    last_error = CharField(null=True)
    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "credential"

    @property
    def cookies_filename(self) -> str:
        return f"{self.id}.txt"


class Board(BaseModel):
    """A Pinterest board (or pin/user URL) queued for archiving."""

    url = CharField(index=True)
    title = CharField(null=True)
    slug = CharField(null=True, index=True)
    status = CharField(default=BoardStatus.pending.value, index=True)

    pin_count = IntegerField(default=0)         # media actually stored
    downloaded_count = IntegerField(default=0)  # succeeded this run
    skipped_count = IntegerField(default=0)     # already-present / duplicate
    error_count = IntegerField(default=0)

    dest_path = CharField(null=True)
    last_error = CharField(null=True)
    log_tail = TextField(null=True)             # last N log lines

    # Include this board in the periodic auto-resync cron.
    auto_resync = BooleanField(default=True)

    # JSON array holding the first ten Pinterest pin ids seen during the latest
    # sync, newest-first. Normal re-syncs stop as soon as any of these ids
    # appears; force_full_sync bypasses that checkpoint.
    sync_checkpoint = CharField(null=True)
    force_full_sync = BooleanField(default=False)

    credential = ForeignKeyField(
        Credential, null=True, backref="boards", column_name="credential_id",
        on_delete="SET NULL", lazy_load=False,
    )

    created_at = UTCDateTimeField(default=utcnow)
    updated_at = UTCDateTimeField(default=utcnow)
    started_at = UTCDateTimeField(null=True)
    finished_at = UTCDateTimeField(null=True)

    class Meta:
        table_name = "board"

    @property
    def display_title(self) -> str:
        """Human-facing name: the real board name once downloaded, else a
        decoded label from the URL, else the (filesystem-safe) slug."""
        return self.title or readable_name_from_url(self.url) or self.slug or "Board"

    @property
    def display_url(self) -> str:
        """The URL with percent-encoded Unicode decoded for display. The href
        keeps the real encoded URL."""
        return unquote(self.url)

    @property
    def progress_pct(self) -> int:
        total = self.pin_count or 0
        if total <= 0:
            return 0
        done = (self.downloaded_count or 0) + (self.skipped_count or 0)
        return min(100, int(done * 100 / total))

    @property
    def is_active(self) -> bool:
        from app.models.enums import ACTIVE_STATUSES

        return self.status in {s.value for s in ACTIVE_STATUSES}


class Pin(BaseModel):
    """A single downloaded media item within a board."""

    board = ForeignKeyField(
        Board, backref="pins", column_name="board_id", index=True,
        on_delete="CASCADE", lazy_load=False,
    )
    pinterest_id = CharField(null=True, index=True)
    filename = CharField()
    rel_path = CharField()                       # relative to boards_dir
    media_type = CharField(default=MediaType.image.value)
    width = IntegerField(null=True)
    height = IntegerField(null=True)
    source_url = CharField(null=True)

    # Searchable metadata pulled from the sidecar.
    title = CharField(null=True)
    description = TextField(null=True)

    # Duplicate detection. sha256 = exact byte match; phash = 256-bit perceptual
    # dHash (hex) that survives re-encoding/resizing so the same image added to
    # different pins is detectable. Videos carry sha256 only.
    content_sha256 = CharField(null=True, index=True)
    phash = CharField(null=True, index=True)
    file_size = IntegerField(null=True)
    # Precomputed duplicate cluster id (shared by all copies of one image);
    # NULL = not a duplicate. Written by the periodic dedup job, read by the
    # duplicates API. See app.services.dedup_service.
    dup_group = IntegerField(null=True, index=True)

    # Which storage backend holds the bytes. Always "local" until the S3 backend
    # in docs/storage-backends.md is implemented.
    storage_backend = CharField(default=StorageBackend.local.value)

    created_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "pin"


class Tag(BaseModel):
    """A user-assigned label. Attaches to both boards and pins."""

    name = CharField(index=True, unique=True)
    created_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "tag"


class BoardTagLink(BaseModel):
    board = ForeignKeyField(
        Board, column_name="board_id", on_delete="CASCADE", lazy_load=False,
    )
    tag = ForeignKeyField(
        Tag, column_name="tag_id", on_delete="CASCADE", lazy_load=False,
    )

    class Meta:
        # Matches the name the original schema created, so an existing
        # pinchive.db keeps its rows after the move off SQLAlchemy.
        table_name = "boardtaglink"
        primary_key = CompositeKey("board", "tag")


class PinTagLink(BaseModel):
    pin = ForeignKeyField(
        Pin, column_name="pin_id", on_delete="CASCADE", lazy_load=False,
    )
    tag = ForeignKeyField(
        Tag, column_name="tag_id", on_delete="CASCADE", lazy_load=False,
    )

    class Meta:
        table_name = "pintaglink"
        primary_key = CompositeKey("pin", "tag")


class Setting(BaseModel):
    """Runtime setting override (key -> value as text). Absent key = use the
    env/config default. See app.services.settings_service."""

    key = CharField(primary_key=True)
    value = TextField()

    class Meta:
        table_name = "setting"


class DeletedPin(BaseModel):
    """Tombstone for a pin removed as a duplicate. Keyed by rel_path (stable
    across re-downloads: the filename is derived from the pin id). A board
    re-sync skips already-archived pins, but non-archived ones (e.g. from an
    interrupted download) would otherwise be re-fetched -- the reconcile checks
    this table and re-removes them so deleted duplicates never come back. Only
    the deleted copy's exact path is blocked, so the kept copy is untouched."""

    rel_path = CharField(primary_key=True)
    deleted_at = UTCDateTimeField(default=utcnow)

    class Meta:
        table_name = "deletedpin"
