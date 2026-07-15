"""Convert UTC database timestamps to the timezone configured by ``TZ``."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


def configured_timezone():
    """Return the IANA timezone selected through the conventional TZ variable."""
    name = os.environ.get("TZ", "UTC").strip() or "UTC"
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return timezone.utc


def local_datetime(value: datetime | None) -> datetime | None:
    """Treat SQLite's naive values as UTC, then convert them for display."""
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(configured_timezone())


def format_datetime(value: datetime | None, fmt: str = "%Y-%m-%d %H:%M") -> str:
    local = local_datetime(value)
    return local.strftime(fmt) if local is not None else "—"
