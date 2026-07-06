"""Runtime-editable settings.

Env/config (app.config.Settings) provides the defaults; the Setting DB table
holds user overrides edited on the /settings page. `effective()` merges them.
Web routes and worker jobs read effective values per request/job, so most
changes apply immediately (page sizes, download tuning, playwright fallback).
Cron intervals apply via the gated hourly crons in app.tasks — also live.
"""

from __future__ import annotations

from datetime import datetime

from sqlmodel import select

from app.config import get_settings
from app.db import session_scope
from app.models import Setting

_base = get_settings()


def _as_bool(v) -> bool:
    return str(v).strip().lower() in ("1", "true", "on", "yes")


# --------------------------------------------------------------------------- #
# crontab schedules (minute hour day-of-month month day-of-week)
# --------------------------------------------------------------------------- #
# We hand-roll a tiny cron matcher (no croniter dep) — the worker ticks once a
# minute and asks "does this expression match now?". Supports *, */n, a-b, a-b/n
# and comma lists. day-of-week is 0-7 with 0 and 7 both meaning Sunday.
_CRON_BOUNDS = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))


def _match_field(field: str, value: int, lo: int, hi: int) -> bool:
    for part in field.split(","):
        part = part.strip()
        rng, sep, step_s = part.partition("/")
        step = int(step_s) if sep else 1
        if step <= 0:
            continue
        if rng == "*":
            start, end = lo, hi
        elif "-" in rng:
            a, _, b = rng.partition("-")
            start, end = int(a), int(b)
        else:
            start = end = int(rng)
        if start <= value <= end and (value - start) % step == 0:
            return True
    return False


def cron_matches(expr: str, dt: datetime) -> bool:
    """True if `dt` matches the 5-field cron expression."""
    parts = expr.split()
    if len(parts) != 5:
        return False
    minute, hour, dom, month, dow = parts
    dow_val = dt.isoweekday() % 7  # Mon=1..Sun=7 -> Sun=0..Sat=6
    try:
        ok = (
            _match_field(minute, dt.minute, 0, 59)
            and _match_field(hour, dt.hour, 0, 23)
            and _match_field(month, dt.month, 1, 12)
        )
        if not ok:
            return False
        dom_ok = _match_field(dom, dt.day, 1, 31)
        dow_ok = _match_field(dow, dow_val, 0, 7) or (
            dow_val == 0 and _match_field(dow, 7, 0, 7)
        )
    except ValueError:
        return False
    # If both day fields are restricted, cron matches when *either* does.
    if dom != "*" and dow != "*":
        return dom_ok or dow_ok
    return dom_ok and dow_ok


def _cron(v) -> str:
    """Validate a cron string; '' means disabled. Raises ValueError on garbage."""
    s = str(v).strip()
    if not s:
        return ""
    parts = s.split()
    if len(parts) != 5:
        raise ValueError(f"cron needs 5 fields: {s!r}")
    for field in parts:
        for part in field.split(","):
            token = part.partition("/")[0]
            if token == "*":
                continue
            for n in token.split("-"):
                if not n.strip().isdigit():
                    raise ValueError(f"bad cron field: {part!r}")
    return s


def cron_due(last_run_key: str, expr: str, now: datetime) -> bool:
    """True if `expr` fires at `now` and hasn't already fired this minute."""
    if not expr or not expr.strip() or not cron_matches(expr, now):
        return False
    raw = get_raw(last_run_key)
    if raw:
        try:
            last = datetime.fromisoformat(raw)
        except ValueError:
            return True
        if last.replace(second=0, microsecond=0) >= now.replace(second=0, microsecond=0):
            return False  # already fired this minute (or clock skew)
    return True


# editable key -> (caster, min, max) ; default comes from the config attr of the
# same name. min/max clamp numeric input; None/None for non-numeric casters.
EDITABLE: dict[str, tuple] = {
    "resync_cron": (_cron, None, None),
    "refresh_cron": (_cron, None, None),
    "dedup_cron": (_cron, None, None),
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
            elif caster is _cron:
                if key not in updates:
                    continue
                try:
                    raw = _cron(updates[key])  # '' allowed (disables the job)
                except (ValueError, TypeError):
                    continue  # keep the previous value on a bad expression
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
