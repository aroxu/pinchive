"""Runtime-editable settings.

Env/config (app.config.Settings) provides the defaults; the Setting DB table
holds user overrides edited on the /settings page. `effective()` merges them.
Web routes and worker jobs read effective values per request/job, so most
changes apply immediately (page sizes, download tuning, playwright fallback).
Cron intervals apply via the gated hourly crons in app.tasks — also live.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.models import Setting

_base = get_settings()


def _as_bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "on", "yes")


# editable key -> (caster, min, max) ; default comes from the config attr of the
# same name. min/max clamp user input.
EDITABLE: dict[str, tuple] = {
    "resync_every_hours": (int, 0, 168),
    "refresh_every_hours": (int, 0, 168),
    "dedup_every_hours": (int, 0, 168),
    "pin_stall_timeout": (int, 30, 7200),
    "dl_sleep": (float, 0.0, 30.0),
    "per_page_boards": (int, 1, 200),
    "per_page_pins": (int, 1, 500),
    "per_page_dupes": (int, 1, 200),
    "use_playwright_fallback": (_as_bool, None, None),
}


def defaults() -> dict:
    return {k: getattr(_base, k) for k in EDITABLE}


def _cast(key: str, raw: str):
    caster, lo, hi = EDITABLE[key]
    val = caster(raw)
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if lo is not None:
            val = max(lo, val)
        if hi is not None:
            val = min(hi, val)
    return val


def effective() -> dict:
    vals = defaults()
    with session_scope() as s:
        for row in s.exec(select(Setting)).all():
            if row.key in EDITABLE:
                try:
                    vals[row.key] = _cast(row.key, row.value)
                except (ValueError, TypeError):
                    pass
    return vals


def get(key: str):
    return effective()[key]


def save(updates: dict) -> None:
    """Persist editable overrides. `use_playwright_fallback` is stored as
    'true'/'false'; unchecked checkbox may be absent -> store 'false'."""
    with session_scope() as s:
        for key, (caster, _lo, _hi) in EDITABLE.items():
            if caster is _as_bool:
                raw = "true" if _as_bool(updates.get(key, "")) else "false"
            elif key in updates and str(updates[key]).strip() != "":
                try:
                    raw = str(_cast(key, str(updates[key])))
                except (ValueError, TypeError):
                    continue
            else:
                continue
            row = s.get(Setting, key)
            if row:
                row.value = raw
            else:
                s.add(Setting(key=key, value=raw))


# --- raw store for internal bookkeeping (cron last-run timestamps) ---
def get_raw(key: str) -> str | None:
    with session_scope() as s:
        row = s.get(Setting, key)
        return row.value if row else None


def set_raw(key: str, value: str) -> None:
    with session_scope() as s:
        row = s.get(Setting, key)
        if row:
            row.value = value
        else:
            s.add(Setting(key=key, value=value))


def due(last_run_key: str, interval_hours: float) -> bool:
    """True if `interval_hours` have elapsed since the timestamp stored under
    `last_run_key` (or if it was never run)."""
    raw = get_raw(last_run_key)
    if not raw:
        return True
    try:
        last = datetime.fromisoformat(raw)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last >= timedelta(hours=interval_hours)
