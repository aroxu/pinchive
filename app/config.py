"""Runtime configuration, loaded from environment (PINCHIVE_* vars)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="PINCHIVE_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    data_dir: Path = Field(default=Path("./data"))
    redis_url: str = Field(default="redis://localhost:6379")

    max_concurrency: int = Field(default=2, ge=1, le=16)
    dl_sleep: float = Field(default=0.8, ge=0.0)

    # No overall board timeout (a big board can take as long as it needs).
    # Instead, guard each individual file: if a single pin download stalls with
    # no data for this many seconds, gallery-dl aborts just that file and moves
    # on to the next pin. Default 600s (10 min).
    pin_stall_timeout: int = Field(default=600, ge=30)

    # Schedules are standard 5-field crontab expressions
    # (minute hour day-of-month month day-of-week); an empty string disables the
    # job. The worker evaluates them every minute (app.tasks._cron_dispatch).
    #
    # Credential keep-alive: re-hit Pinterest and persist the rotated session
    # cookie so a registered credential stays alive. Default: every 6 hours.
    refresh_cron: str = Field(default="0 */6 * * *")

    # Optional Playwright re-login fallback: when a session is genuinely dead
    # (server-side logout), try a headless browser login to mint fresh cookies.
    # Off by default; requires the image built with the browser (INSTALL_PLAYWRIGHT)
    # and a stored login profile. See docs/credential-refresh.md.
    use_playwright_fallback: bool = Field(default=False)

    # Automatic board re-sync: periodically re-download boards to pick up new
    # pins (cheap — the per-board archive means only new pins are fetched).
    # Boards can opt out individually (Board.auto_resync). Default: daily 04:30.
    resync_cron: str = Field(default="30 4 * * *")

    # How often the worker recomputes + stores duplicate groups (the manual
    # Rescan button still works regardless). Default: every 6 hours at :45.
    dedup_cron: str = Field(default="45 */6 * * *")

    # UI page sizes.
    per_page_boards: int = Field(default=24, ge=1, le=200)
    per_page_pins: int = Field(default=60, ge=1, le=500)
    per_page_dupes: int = Field(default=20, ge=1, le=200)

    # ---- derived paths ----
    @property
    def db_path(self) -> Path:
        return self.data_dir / "pinchive.db"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"

    @property
    def boards_dir(self) -> Path:
        return self.data_dir / "boards"

    @property
    def cookies_dir(self) -> Path:
        return self.data_dir / "cookies"

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.boards_dir, self.cookies_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
