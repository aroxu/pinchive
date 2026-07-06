"""Runtime-editable settings store."""

from datetime import datetime, timezone

from app import appsettings
from app.config import get_settings


def test_defaults_match_config():
    d = appsettings.defaults()
    cfg = get_settings()
    assert d["per_page_boards"] == cfg.per_page_boards
    assert d["resync_cron"] == cfg.resync_cron


def test_save_and_effective_override():
    appsettings.save({
        "per_page_boards": "7",
        "pin_stall_timeout": "120",
        "use_playwright_fallback": "true",
    })
    eff = appsettings.effective()
    assert eff["per_page_boards"] == 7
    assert eff["pin_stall_timeout"] == 120
    assert eff["use_playwright_fallback"] is True


def test_save_clamps_and_ignores_blank():
    appsettings.save({"per_page_boards": "9999"})   # -> clamped to 200
    assert appsettings.effective()["per_page_boards"] == 200
    appsettings.save({"per_page_pins": ""})         # blank -> unchanged
    assert appsettings.effective()["per_page_pins"] == \
        appsettings.defaults()["per_page_pins"]


def test_bool_unchecked_saves_false():
    appsettings.save({"use_playwright_fallback": "true"})
    assert appsettings.effective()["use_playwright_fallback"] is True
    appsettings.save({})  # checkbox absent -> stored false
    assert appsettings.effective()["use_playwright_fallback"] is False


def test_cron_matches():
    dt = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)  # Monday 03:30
    assert appsettings.cron_matches("30 3 * * *", dt) is True
    assert appsettings.cron_matches("30 4 * * *", dt) is False
    assert appsettings.cron_matches("*/30 * * * *", dt) is True   # 0,30
    assert appsettings.cron_matches("0 */6 * * *", dt) is False   # minute 0 only
    assert appsettings.cron_matches("30 3 * * 1", dt) is True     # Monday
    assert appsettings.cron_matches("30 3 * * 0", dt) is False    # Sunday
    assert appsettings.cron_matches("bad expr", dt) is False


def test_cron_due_gate_and_disabled():
    now = datetime(2026, 7, 6, 3, 30, tzinfo=timezone.utc)
    assert appsettings.cron_due("cron_never", "30 3 * * *", now) is True
    assert appsettings.cron_due("cron_x", "", now) is False        # disabled
    assert appsettings.cron_due("cron_x", "0 4 * * *", now) is False  # not now
    appsettings.set_raw("cron_ran", now.isoformat())
    assert appsettings.cron_due("cron_ran", "30 3 * * *", now) is False  # this min


def test_cron_save_roundtrip_and_disable():
    appsettings.save({"resync_cron": "15 2 * * *"})
    assert appsettings.effective()["resync_cron"] == "15 2 * * *"
    appsettings.save({"resync_cron": ""})            # empty -> disabled
    assert appsettings.effective()["resync_cron"] == ""
    appsettings.save({"resync_cron": "not a cron"})  # invalid -> unchanged
    assert appsettings.effective()["resync_cron"] == ""
