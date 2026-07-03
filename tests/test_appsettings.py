"""Runtime-editable settings store."""

from datetime import datetime, timezone

from app import appsettings
from app.config import get_settings


def test_defaults_match_config():
    d = appsettings.defaults()
    cfg = get_settings()
    assert d["per_page_boards"] == cfg.per_page_boards
    assert d["resync_every_hours"] == cfg.resync_every_hours


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


def test_due_gate():
    assert appsettings.due("never_run_key", 6) is True
    appsettings.set_raw("k", datetime.now(timezone.utc).isoformat())
    assert appsettings.due("k", 6) is False
