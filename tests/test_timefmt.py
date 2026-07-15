from datetime import datetime, timezone

from app.timefmt import format_datetime, local_datetime


def test_naive_sqlite_datetime_uses_configured_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Seoul")
    value = datetime(2026, 7, 15, 3, 30)

    assert format_datetime(value) == "2026-07-15 12:30"


def test_aware_utc_datetime_uses_configured_timezone(monkeypatch):
    monkeypatch.setenv("TZ", "Asia/Seoul")
    value = datetime(2026, 7, 15, 3, 30, tzinfo=timezone.utc)

    converted = local_datetime(value)
    assert converted is not None
    assert converted.utcoffset().total_seconds() == 9 * 60 * 60


def test_invalid_timezone_falls_back_to_utc(monkeypatch):
    monkeypatch.setenv("TZ", "Not/A_Timezone")
    value = datetime(2026, 7, 15, 3, 30)

    assert format_datetime(value) == "2026-07-15 03:30"
