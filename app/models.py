"""Database models.

Note: intentionally NOT using `from __future__ import annotations`. PEP 563
stringifies annotations, which breaks SQLModel's Relationship type resolution
(`list["Board"]` gets passed to SQLAlchemy as a literal generic).
"""

from datetime import datetime, timezone
from enum import Enum
from typing import List, Optional

from sqlmodel import Field, Relationship, SQLModel


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


class BoardTagLink(SQLModel, table=True):
    board_id: Optional[int] = Field(
        default=None, foreign_key="board.id", primary_key=True
    )
    tag_id: Optional[int] = Field(
        default=None, foreign_key="tag.id", primary_key=True
    )


class PinTagLink(SQLModel, table=True):
    pin_id: Optional[int] = Field(
        default=None, foreign_key="pin.id", primary_key=True
    )
    tag_id: Optional[int] = Field(
        default=None, foreign_key="tag.id", primary_key=True
    )


class Credential(SQLModel, table=True):
    """A stored Pinterest session (cookies) used to reach private boards.

    The cookies themselves live on disk as a Netscape cookies.txt at
    settings.cookies_dir / f"{id}.txt" — not in the DB — so gallery-dl can
    consume the file directly and secrets stay out of the sqlite dump.
    """

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True)
    status: CredentialStatus = Field(default=CredentialStatus.unchecked)
    # Pinterest username, informational only (auth is cookie-based).
    account: Optional[str] = Field(default=None)
    note: Optional[str] = Field(default=None)
    last_checked_at: Optional[datetime] = Field(default=None)
    last_error: Optional[str] = Field(default=None)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    boards: List["Board"] = Relationship(back_populates="credential")

    @property
    def cookies_filename(self) -> str:
        return f"{self.id}.txt"


class Board(SQLModel, table=True):
    """A Pinterest board (or pin/user URL) queued for archiving."""

    id: Optional[int] = Field(default=None, primary_key=True)
    url: str = Field(index=True)
    title: Optional[str] = Field(default=None)
    slug: Optional[str] = Field(default=None, index=True)
    status: BoardStatus = Field(default=BoardStatus.pending, index=True)

    pin_count: int = Field(default=0)          # discovered
    downloaded_count: int = Field(default=0)   # succeeded
    skipped_count: int = Field(default=0)      # already-present / duplicate
    error_count: int = Field(default=0)

    dest_path: Optional[str] = Field(default=None)
    last_error: Optional[str] = Field(default=None)
    log_tail: Optional[str] = Field(default=None)  # last N log lines

    # Include this board in the periodic auto-resync cron.
    auto_resync: bool = Field(default=True)

    credential_id: Optional[int] = Field(
        default=None, foreign_key="credential.id"
    )
    credential: Optional[Credential] = Relationship(back_populates="boards")

    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    started_at: Optional[datetime] = Field(default=None)
    finished_at: Optional[datetime] = Field(default=None)

    pins: List["Pin"] = Relationship(
        back_populates="board",
        sa_relationship_kwargs={"cascade": "all, delete-orphan"},
    )
    tags: List["Tag"] = Relationship(
        back_populates="boards", link_model=BoardTagLink
    )

    @property
    def progress_pct(self) -> int:
        total = self.pin_count or 0
        if total <= 0:
            return 0
        done = self.downloaded_count + self.skipped_count
        return min(100, int(done * 100 / total))


class Pin(SQLModel, table=True):
    """A single downloaded media item within a board."""

    id: Optional[int] = Field(default=None, primary_key=True)
    board_id: int = Field(foreign_key="board.id", index=True)
    pinterest_id: Optional[str] = Field(default=None, index=True)
    filename: str
    rel_path: str                      # relative to boards_dir
    media_type: str = Field(default="image")  # image | video
    width: Optional[int] = Field(default=None)
    height: Optional[int] = Field(default=None)
    source_url: Optional[str] = Field(default=None)

    # Searchable metadata pulled from the sidecar.
    title: Optional[str] = Field(default=None)
    description: Optional[str] = Field(default=None)

    # Duplicate detection. sha256 = exact byte match; phash = 64-bit perceptual
    # dHash (hex) that survives re-encoding/resizing so the same image added to
    # different pins is detectable. Videos carry sha256 only.
    content_sha256: Optional[str] = Field(default=None, index=True)
    phash: Optional[str] = Field(default=None, index=True)
    file_size: Optional[int] = Field(default=None)

    created_at: datetime = Field(default_factory=utcnow)

    board: Optional[Board] = Relationship(back_populates="pins")
    tags: List["Tag"] = Relationship(
        back_populates="pins", link_model=PinTagLink
    )


class Tag(SQLModel, table=True):
    """A user-assigned label. Attaches to both boards and pins."""

    id: Optional[int] = Field(default=None, primary_key=True)
    name: str = Field(index=True, unique=True)
    created_at: datetime = Field(default_factory=utcnow)

    boards: List["Board"] = Relationship(
        back_populates="tags", link_model=BoardTagLink
    )
    pins: List["Pin"] = Relationship(
        back_populates="tags", link_model=PinTagLink
    )
