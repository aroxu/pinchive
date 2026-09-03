"""Wire format for the JSON API consumed by the React front end."""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

from app.core.timefmt import local_datetime
from app.models import Board, Pin
from app.services.storage import get_storage

T = TypeVar("T")


class PageMeta(BaseModel):
    page: int
    pages: int
    total: int


class PageOut(BaseModel, Generic[T]):
    items: list[T]
    meta: PageMeta


class CredentialOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    status: str
    note: str | None = None
    last_error: str | None = None
    last_checked_at: datetime | None = None

    @classmethod
    def of(cls, cred) -> "CredentialOut":
        return cls(
            id=cred.id,
            name=cred.name,
            status=cred.status,
            note=cred.note,
            last_error=cred.last_error,
            last_checked_at=local_datetime(cred.last_checked_at),
        )


class BoardOut(BaseModel):
    id: int
    url: str
    display_url: str
    title: str
    slug: str | None = None
    status: str
    is_active: bool
    pin_count: int
    downloaded_count: int
    skipped_count: int
    error_count: int
    progress_pct: int
    auto_resync: bool
    last_error: str | None = None
    log_tail: str | None = None
    credential_id: int | None = None
    covers: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    finished_at: datetime | None = None

    @classmethod
    def of(
        cls,
        board: Board,
        *,
        covers: list[str] | None = None,
        tags: list[str] | None = None,
        include_log: bool = False,
    ) -> "BoardOut":
        storage = get_storage()
        return cls(
            id=board.id,
            url=board.url,
            display_url=board.display_url,
            title=board.display_title,
            slug=board.slug,
            status=board.status,
            is_active=board.is_active,
            pin_count=board.pin_count or 0,
            downloaded_count=board.downloaded_count or 0,
            skipped_count=board.skipped_count or 0,
            error_count=board.error_count or 0,
            progress_pct=board.progress_pct,
            auto_resync=bool(board.auto_resync),
            last_error=board.last_error,
            log_tail=board.log_tail if include_log else None,
            credential_id=board.credential_id,
            covers=[storage.url_for(p) for p in (covers or [])],
            tags=tags or [],
            created_at=local_datetime(board.created_at),
            updated_at=local_datetime(board.updated_at),
            finished_at=local_datetime(board.finished_at),
        )


class PinOut(BaseModel):
    id: int
    board_id: int
    filename: str
    rel_path: str
    url: str
    media_type: str
    width: int | None = None
    height: int | None = None
    title: str | None = None
    description: str | None = None
    source_url: str | None = None
    file_size: int | None = None
    dup_group: int | None = None
    storage_backend: str = "local"
    tags: list[str] = Field(default_factory=list)

    @classmethod
    def of(cls, pin: Pin, *, tags: list[str] | None = None) -> "PinOut":
        return cls(
            id=pin.id,
            board_id=pin.board_id,
            filename=pin.filename,
            rel_path=pin.rel_path,
            url=get_storage().url_for(pin.rel_path),
            media_type=pin.media_type,
            width=pin.width,
            height=pin.height,
            title=pin.title,
            description=pin.description,
            source_url=pin.source_url,
            file_size=pin.file_size,
            dup_group=pin.dup_group,
            storage_backend=pin.storage_backend or "local",
            tags=tags or [],
        )


class DuplicateGroupOut(BaseModel):
    group_id: int
    keeper_id: int
    pins: list[PinOut]


class StatsOut(BaseModel):
    boards: int
    pins: int
    sessions: int


class SidebarCountsOut(BaseModel):
    boards: int
    creds: int
    creds_active: int
    syncing: int
    removable: int


class SettingsOut(BaseModel):
    values: dict
    defaults: dict
    editable: list[str]
    max_concurrency: int
    queue_up: bool


class HealthOut(BaseModel):
    status: str
    queue: bool


# --- request bodies ---------------------------------------------------------
class BoardCreateIn(BaseModel):
    url: str
    credential_id: int | None = None
    auto: bool = False


class CredentialCreateIn(BaseModel):
    name: str
    cookies: str


class TagIn(BaseModel):
    name: str


class BulkTagIn(BaseModel):
    pin_ids: list[int]
    name: str = ""
    action: str = "add"   # add | remove | delete


class ResolveDuplicatesIn(BaseModel):
    pin_ids: list[int] = Field(default_factory=list)
    all: bool = False
